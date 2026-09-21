"""需知评测：把评测集里的每条需求跑一遍规则引擎（不调模型，可复现），对照专家期望打分，出成绩单。

用法：
    python tools/eval_req.py                      # 跑 data/eval/eval_set.json，写 data/eval/eval_report.md
    python tools/eval_req.py 我的评测集.json      # 换成公司真实需求（脱敏后）
    python tools/eval_req.py --json               # 额外输出 JSON 汇总（给 PPT / 方案文档取数）
    python tools/eval_req.py --strict             # 低于阈值时退出码非 0（CI 用）

评测集每条：{"id", "dept", "channel", "text", "expect": {...}}，expect 各项都可省略：
    type            允许的需求类型列表           title_any        标题应包含其一
    data_any        数据来源应覆盖的关键词       indicators_any   应抽到的指标其一
    symbols_any     应抽到的品种其一             probes_must      必须问到的追问编号（futures_probes.json 的 id）
    probes_must_not 不该问的追问编号             decisions_must   必须给出的 IT 决策编号
    days            专家工时区间 [低, 高]         realtime         需求是否要求实时（对照定架的时效判断）
    redact_must_not_contain  脱敏后文本里不得出现的内容
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xuzhi import config  # noqa: E402
from xuzhi.llm import LLM  # noqa: E402
from xuzhi.pipeline import analyze  # noqa: E402

DEFAULT_SET = config.DATA_DIR / "eval" / "eval_set.json"
DEFAULT_REPORT = config.DATA_DIR / "eval" / "eval_report.md"
THRESHOLDS = {"type_acc": 0.85, "probe_cov": 0.85, "probe_false": 0.10, "redact": 1.0, "decision_cov": 0.9}


def _rate(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def score_case(case: dict, llm: LLM) -> dict:
    exp = case.get("expect") or {}
    t0 = time.time()
    a = analyze(case["text"], llm=llm, llm_polish=False)
    secs = round(time.time() - t0, 3)
    card, est, arch = a.card, a.estimate, a.architecture
    qids = {q.id for q in a.questions}
    dids = {d.id for d in arch.decisions}
    r: dict = {"id": case["id"], "dept": case.get("dept", ""), "title": card.title, "type": card.req_type,
               "n_questions": len(a.questions), "mid": est.mid, "ai_mid": est.ai_mid, "seconds": secs, "notes": []}
    if exp.get("type"):
        r["type_ok"] = card.req_type in exp["type"]
        if not r["type_ok"]:
            r["notes"].append(f"类型 {card.req_type}≠{'/'.join(exp['type'])}")
    if exp.get("title_any"):
        r["title_ok"] = any(k in card.title for k in exp["title_any"])
        if not r["title_ok"]:
            r["notes"].append("标题未命中")
    if exp.get("data_any"):
        hits = [k for k in exp["data_any"] if any(k in d for d in card.data_sources)]
        r["data_cov"] = len(hits) / len(exp["data_any"])
        miss = [k for k in exp["data_any"] if k not in hits]
        if miss:
            r["notes"].append("数据来源漏：" + "/".join(miss))
    if exp.get("indicators_any"):
        r["ind_ok"] = any(k in card.indicators for k in exp["indicators_any"])
        if not r["ind_ok"]:
            r["notes"].append("指标未抽到")
    if exp.get("symbols_any"):
        r["sym_ok"] = any(k in card.symbols for k in exp["symbols_any"])
        if not r["sym_ok"]:
            r["notes"].append("品种未抽到")
    if exp.get("probes_must"):
        got = [p for p in exp["probes_must"] if p in qids]
        r["probe_cov"] = len(got) / len(exp["probes_must"])
        miss = [p for p in exp["probes_must"] if p not in qids]
        if miss:
            r["notes"].append("漏问：" + "/".join(miss))
    if exp.get("probes_must_not"):
        bad = [p for p in exp["probes_must_not"] if p in qids]
        r["probe_false"] = len(bad) / len(exp["probes_must_not"])
        if bad:
            r["notes"].append("误问：" + "/".join(bad))
    if exp.get("decisions_must"):
        got = [d for d in exp["decisions_must"] if d in dids]
        r["decision_cov"] = len(got) / len(exp["decisions_must"])
        miss = [d for d in exp["decisions_must"] if d not in dids]
        if miss:
            r["notes"].append("缺决策：" + "/".join(miss))
    if exp.get("days"):
        lo, hi = exp["days"]
        r["days_ok"] = lo <= est.mid <= hi
        r["days_expert"] = f"{lo}～{hi}"
        if not r["days_ok"]:
            r["notes"].append(f"估算 {est.mid} 不在 {lo}～{hi}")
    if "realtime" in exp:
        d2 = next((d for d in arch.decisions if d.id == "D2"), None)
        want_rt = bool(exp["realtime"])
        got_rt = bool(d2 and "实时" in d2.recommended)
        r["realtime_ok"] = (want_rt == got_rt)
        if not r["realtime_ok"]:
            r["notes"].append("时效判断不符")
    if exp.get("redact_must_not_contain"):
        leaked = [s for s in exp["redact_must_not_contain"] if s in a.redacted_text]
        r["redact_ok"] = not leaked
        if leaked:
            r["notes"].append("脱敏漏：" + "/".join(leaked))
    dm = getattr(arch, "data_map", []) or []
    r["map_located"] = round(sum(1 for x in dm if x.system != "—") / len(dm), 2) if dm else None
    return r


def summarize(rows: list[dict]) -> dict:
    def col(k):
        return [float(r[k]) for r in rows if k in r and r[k] is not None]
    s = {
        "cases": len(rows),
        "type_acc": _rate(col("type_ok")),
        "title_acc": _rate(col("title_ok")),
        "data_cov": _rate(col("data_cov")),
        "indicator_acc": _rate(col("ind_ok")),
        "symbol_acc": _rate(col("sym_ok")),
        "probe_cov": _rate(col("probe_cov")),
        "probe_false": _rate(col("probe_false")),
        "decision_cov": _rate(col("decision_cov")),
        "days_in_range": _rate(col("days_ok")),
        "realtime_acc": _rate(col("realtime_ok")),
        "redact": _rate(col("redact_ok")),
        "map_located": _rate(col("map_located")),
        "avg_questions": round(statistics.mean([r["n_questions"] for r in rows]), 1) if rows else 0,
        "avg_seconds": round(statistics.mean([r["seconds"] for r in rows]), 3) if rows else 0,
        "avg_saving_pct": round(100 * (1 - statistics.mean([r["ai_mid"] / r["mid"] for r in rows if r["mid"]])), 0) if rows else 0,
    }
    s["pass"] = all(
        (s[k] is None) or (s[k] <= v if k == "probe_false" else s[k] >= v) for k, v in THRESHOLDS.items()
    )
    return s


LABELS = {"type_acc": "类型识别准确率", "title_acc": "标题命中率", "data_cov": "数据来源覆盖率", "indicator_acc": "指标抽取命中率",
          "symbol_acc": "品种抽取命中率", "probe_cov": "追问覆盖率（专家必问）", "probe_false": "误问率（专家不该问）",
          "decision_cov": "IT 决策覆盖率", "days_in_range": "估算落在专家区间", "realtime_acc": "时效判断准确率",
          "redact": "脱敏无漏检", "map_located": "数据地图定位率"}


def _pct(v):
    return "—" if v is None else f"{100 * v:.0f}%"


def render_report(rows: list[dict], s: dict, src: Path) -> str:
    md = [f"# 需知评测成绩单", f"评测集：`{src.name}`（{s['cases']} 条，全部虚构）　引擎：规则（不调模型，可复现）　生成：tools/eval_req.py", "",
          "## 汇总", "| 指标 | 结果 | 阈值 |", "|---|---|---|"]
    for k, label in LABELS.items():
        th = THRESHOLDS.get(k)
        th_s = "" if th is None else (f"≤ {100 * th:.0f}%" if k == "probe_false" else f"≥ {100 * th:.0f}%")
        md.append(f"| {label} | {_pct(s[k])} | {th_s} |")
    md += [f"| 平均追问条数 | {s['avg_questions']} | |", f"| 平均出稿用时 | {s['avg_seconds']} s | |",
           f"| AI 协同平均省时 | {s['avg_saving_pct']:.0f}% | |", f"| **总体** | {'通过' if s['pass'] else '未达阈值'} | |", "",
           "## 逐条", "| 编号 | 部门 | 识别标题 | 类型 | 问题数 | 传统 / AI 人天 | 专家区间 | 备注 |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['id']} | {r['dept']} | {r['title']} | {r['type']} | {r['n_questions']} | {r['mid']} / {r['ai_mid']} | {r.get('days_expert', '—')} | {'；'.join(r['notes']) or '✅'} |")
    md += ["", "口径：类型 / 标题 / 指标 / 品种按抽取结果对照专家期望；追问覆盖率 = 专家必问的追问被问到的比例，误问率 = 专家标注不该问的追问却问了的比例；",
           "估算落区间 = 规则估算中位数落在专家区间内的比例（专家区间为虚构样例的人工标注，真实数据接入后以实际人天为准）；脱敏无漏检 = 隐盾后文本不含标注的敏感内容。"]
    return "\n".join(md)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("set", nargs="?", default=str(DEFAULT_SET))
    ap.add_argument("--report", default=str(DEFAULT_REPORT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    src = Path(a.set)
    cases = json.loads(src.read_text(encoding="utf-8"))["cases"]
    llm = LLM(mode="mock")
    rows = [score_case(c, llm) for c in cases]
    s = summarize(rows)
    report = render_report(rows, s, src)
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report).write_text(report, encoding="utf-8")
    print(report.split("## 逐条")[0])
    print(f"逐条结果见 {a.report}")
    if a.json:
        print(json.dumps(s, ensure_ascii=False))
    return 0 if (s["pass"] or not a.strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())

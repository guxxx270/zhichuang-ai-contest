"""隐盾评测：python tools/eval.py [--md data/eval_report.md]

读 data/eval_cases.json（全部虚构），输出成绩单：
  敏感项检出率   main 里 secrets 换掉的比例
  误伤           main 的 keep 被改动的条数 + negative 被改动的条数（一个字都不该改）
  往返一致       restore(redact(原文)) == 原文
  容错还原       把脱敏文本里的标签轮流改成 8 种常见写法（模拟大模型改写），还原后仍等于原文
  速度           单条平均用时；1 万字长文本一次脱敏用时
boundary 是已知边界，单列检出情况，不计入主指标。
holdout / holdout_negative 是留出集：规则定稿前写好、定稿后只跑一次，单列结果，不针对它改规则。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yindun import __version__  # noqa: E402
from yindun.privacy import Redactor  # noqa: E402

_LABEL = re.compile(r"<([^\s<>_]{1,8})_(\d+)>")
_FULL = str.maketrans("0123456789", "０１２３４５６７８９")
VARIANTS = [
    lambda k, n: f"<{k}_{n}>",
    lambda k, n: f"〈{k}_{n}〉",
    lambda k, n: f"【{k}_{n}】",
    lambda k, n: f"\\<{k}\\_{n}\\>",
    lambda k, n: f"&lt;{k}_{n}&gt;",
    lambda k, n: f"<{k}{n}>",
    lambda k, n: f"{k}_{n}",
    lambda k, n: f"＜{k}＿{str(n).translate(_FULL)}＞",
]


def rewrite_labels(text: str, offset: int) -> str:
    """模拟大模型改写标签：第 i 个标签用第 (i + offset) 种写法。"""
    i = 0

    def sub(m: re.Match) -> str:
        nonlocal i
        out = VARIANTS[(i + offset) % len(VARIANTS)](m.group(1), m.group(2))
        i += 1
        return out

    return _LABEL.sub(sub, text)


def holdout(cases: dict, red: Redactor) -> dict:
    hits = total = keep_bad = keep_total = neg_ok = 0
    fails = []
    for c in cases.get("holdout", []):
        out = red.redact(c["text"]).text
        for s in c["secrets"]:
            total += 1
            if s in out:
                fails.append(f"{c['id']} 漏：{s}")
            else:
                hits += 1
        for k in c["keep"]:
            keep_total += 1
            if k not in out:
                keep_bad += 1
                fails.append(f"{c['id']} 误伤：{k} → {out}")
    negs = cases.get("holdout_negative", [])
    for c in negs:
        out = red.redact(c["text"]).text
        if out == c["text"]:
            neg_ok += 1
        else:
            fails.append(f"{c['id']} 误改：{out}")
    return {"n": len(cases.get("holdout", [])), "hits": hits, "total": total, "keep_bad": keep_bad, "keep_total": keep_total,
            "neg": len(negs), "neg_ok": neg_ok, "fails": fails}


def run(cases: dict) -> dict:
    red = Redactor()
    main, neg, bnd = cases["main"], cases["negative"], cases["boundary"]
    secrets_total = secrets_hit = keep_total = keep_broken = 0
    roundtrip_ok = tolerant_ok = labels_total = 0
    misses, broken, rt_fail, tol_fail, neg_fail = [], [], [], [], []
    times = []
    for i, c in enumerate(main):
        t0 = time.perf_counter()
        r = red.redact(c["text"])
        times.append((time.perf_counter() - t0) * 1000)
        for s in c["secrets"]:
            secrets_total += 1
            if s in r.text:
                misses.append(f"{c['id']} 漏：{s}")
            else:
                secrets_hit += 1
        for k in c["keep"]:
            keep_total += 1
            if k not in r.text:
                keep_broken += 1
                broken.append(f"{c['id']} 误伤：{k} → {r.text}")
        if Redactor.restore(r.text, r.mapping) == c["text"]:
            roundtrip_ok += 1
        else:
            rt_fail.append(c["id"])
        labels_total += len(_LABEL.findall(r.text))
        if Redactor.restore(rewrite_labels(r.text, i), r.mapping) == c["text"]:
            tolerant_ok += 1
        else:
            tol_fail.append(f"{c['id']}：{rewrite_labels(r.text, i)}")
    neg_ok = 0
    for c in neg:
        t0 = time.perf_counter()
        out = red.redact(c["text"]).text
        times.append((time.perf_counter() - t0) * 1000)
        if out == c["text"]:
            neg_ok += 1
        else:
            neg_fail.append(f"{c['id']}：{out}")
    bnd_rows = []
    for c in bnd:
        out = red.redact(c["text"]).text
        hit = all(s not in out for s in c["secrets"])
        bnd_rows.append((c["id"], c["scene"], "认出" if hit else "未认出", c["why"]))
    long_text = "\n".join(c["text"] for c in main + neg)
    long_text = (long_text * (10000 // len(long_text) + 1))[:10000]
    t0 = time.perf_counter()
    red.redact(long_text)
    long_ms = (time.perf_counter() - t0) * 1000
    return {
        "version": __version__, "main": len(main), "negative": len(neg), "boundary": len(bnd),
        "secrets_total": secrets_total, "secrets_hit": secrets_hit, "keep_total": keep_total, "keep_broken": keep_broken,
        "neg_ok": neg_ok, "roundtrip_ok": roundtrip_ok, "tolerant_ok": tolerant_ok, "labels_total": labels_total,
        "avg_ms": statistics.mean(times), "max_ms": max(times), "long_ms": long_ms,
        "holdout": holdout(cases, red),
        "misses": misses, "broken": broken, "rt_fail": rt_fail, "tol_fail": tol_fail, "neg_fail": neg_fail, "boundary_rows": bnd_rows,
    }


def report(s: dict) -> str:
    pct = lambda a, b: f"{a / b * 100:.1f}%" if b else "—"   # noqa: E731
    bnd_hit = sum(1 for r in s["boundary_rows"] if r[2] == "认出")
    lines = [
        f"# 隐盾评测成绩单（v{s['version']}）",
        "",
        f"评测集 `data/eval_cases.json`：主用例 {s['main']} 条（含 {s['secrets_total']} 个敏感项、{s['keep_total']} 个必须保留的业务词），"
        f"无敏感信息的业务文本 {s['negative']} 条，已知边界 {s['boundary']} 条。全部虚构，与规则同步编写，属于开发集。",
        "",
        "| 指标 | 结果 | 说明 |",
        "|---|---|---|",
        f"| 敏感项检出率 | {pct(s['secrets_hit'], s['secrets_total'])}（{s['secrets_hit']}/{s['secrets_total']}） | 主用例里应换掉的姓名、号码、地址、密钥、金额 |",
        f"| 业务词误伤 | {s['keep_broken']}/{s['keep_total']} | 主用例里必须原样保留的业务词被改动的个数 |",
        f"| 无敏感文本误改 | {s['negative'] - s['neg_ok']}/{s['negative']} | 行情、规则、办公文本，一个字都不该改 |",
        f"| 往返一致 | {pct(s['roundtrip_ok'], s['main'])} | 脱敏后再还原，与原文逐字相同 |",
        f"| 容错还原 | {pct(s['tolerant_ok'], s['main'])} | {s['labels_total']} 个标签轮流改成 8 种写法（〈〉【】、Markdown 转义、HTML 实体、少下划线、去括号、全角）后还原 |",
        f"| 单条用时 | 平均 {s['avg_ms']:.2f} ms，最长 {s['max_ms']:.2f} ms | 本机 CPU，纯规则 |",
        f"| 1 万字一次脱敏 | {s['long_ms']:.0f} ms | |",
    ]
    h = s["holdout"]
    if h["n"]:
        lines += [
            "",
            f"## 留出集（{h['n']} 条 + 无敏感文本 {h['neg']} 条：规则定稿前写好，定稿后只跑这一次）",
            "",
            "| 指标 | 结果 |",
            "|---|---|",
            f"| 敏感项检出率 | {pct(h['hits'], h['total'])}（{h['hits']}/{h['total']}） |",
            f"| 业务词误伤 | {h['keep_bad']}/{h['keep_total']} |",
            f"| 无敏感文本误改 | {h['neg'] - h['neg_ok']}/{h['neg']} |",
            "",
            "未通过：" + ("；".join(h["fails"]) if h["fails"] else "无"),
        ]
    lines += [
        "",
        f"## 已知边界（{bnd_hit}/{s['boundary']} 认出，不计入上表）",
        "",
        "| 编号 | 写法 | 结果 | 原因 / 对策 |",
        "|---|---|---|---|",
        *[f"| {a} | {b} | {c} | {d} |" for a, b, c, d in s["boundary_rows"]],
    ]
    fails = s["misses"] + s["broken"] + s["neg_fail"] + [f"往返不一致：{x}" for x in s["rt_fail"]] + [f"容错还原失败：{x}" for x in s["tol_fail"]]
    lines += ["", "## 未通过明细", ""] + ([f"- {f}" for f in fails] if fails else ["无。"])
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--cases", default=str(ROOT / "data" / "eval_cases.json"))
    p.add_argument("--md", help="成绩单另存为 Markdown")
    a = p.parse_args()
    s = run(json.loads(Path(a.cases).read_text(encoding="utf-8")))
    md = report(s)
    print(md)
    if a.md:
        Path(a.md).write_text(md, encoding="utf-8")


if __name__ == "__main__":
    main()

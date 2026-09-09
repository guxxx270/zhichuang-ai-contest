"""把公司真实数据导入需知的知识库（全部在本机完成，不经过任何外部服务）。

用法（在 需知/ 目录下）：
  python tools/import_knowledge.py template 填好的模板.xlsx          # 三个 sheet 一次导入（历史需求 / 追问规则 / 系统目录）
  python tools/import_knowledge.py tickets  工单导出.xlsx [--sheet 名]  # 工单系统原始导出 → 历史需求库（自动识别列名、自动抽关键词）
  加 --dry-run 只预览不写入。写入前自动把原 JSON 备份为 *.bak。

工单导出的列名不必完全一致，脚本按常见叫法自动识别：
  标题：需求标题 / 标题 / 需求名称 / 主题 / 工单标题
  部门：提出部门 / 部门 / 申请部门 / 需求方
  类型：类型 / 需求类型 / 分类
  估算：估算人天 / 预估人天 / 预估工时 / 估算工时（小时会按 8 小时折算成人天）
  实际：实际人天 / 实际工时 / 工时 / 耗时
  年份：年份 / 完成日期 / 上线日期 / 创建日期（取年份）
  描述：描述 / 需求描述 / 内容（用于抽关键词）
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from xuzhi import config, textutil  # noqa: E402

ALIASES = {
    "title": ["需求标题", "标题", "需求名称", "主题", "工单标题", "名称"],
    "dept": ["提出部门", "部门", "申请部门", "需求方", "提出方"],
    "type": ["类型", "需求类型", "分类", "工单类型"],
    "estimate": ["当时估算人天", "估算人天", "预估人天", "预估工时", "估算工时", "预估"],
    "actual": ["实际人天", "实际工时", "工时", "耗时", "实际"],
    "year": ["年份", "完成日期", "上线日期", "创建日期", "日期"],
    "desc": ["描述", "需求描述", "内容", "详情", "备注"],
    "keywords": ["关键词（顿号分隔）", "关键词"],
    "ai": ["是否用AI（是/否）", "是否用AI", "是否用 AI"],
    "ai_share": ["AI参与度（0～1）", "AI参与度", "AI 参与度"],
    "note": ["备注（返工/口径问题等）", "备注", "说明"],
    "id": ["编号", "ID", "工单号"],
}
TYPE_RULES = [(r"日报|周报|月报|报表|报告", "报表"), (r"页面|看板|查询|展示|H5|检索", "页面"), (r"提醒|预警|告警|通知|推送|提示", "提醒"),
              (r"接口|API|对接", "接口"), (r"流程|审批", "流程"), (r"抓取|采集|落库|测算|同步|清洗|数据", "数据"), (r"改版|优化|升级|改造", "改造")]


def _col(df: pd.DataFrame, key: str) -> str | None:
    cols = {str(c).strip(): c for c in df.columns}
    for a in ALIASES[key]:
        for name, c in cols.items():
            if name == a or name.startswith(a):
                return c
    return None


def _days(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    m = re.search(r"[\d.]+", s)
    if not m:
        return None
    n = float(m.group(0))
    if "小时" in s or s.endswith("h") or "工时" in s and n > 60:
        n = round(n / 8, 1)
    return n


def _year(v) -> int:
    m = re.search(r"(20\d{2})", str(v))
    return int(m.group(1)) if m else 0


def _type(title: str, given: str) -> str:
    g = str(given or "").strip()
    if g in ("报表", "页面", "提醒", "接口", "数据", "流程", "改造"):
        return g
    for pat, label in TYPE_RULES:
        if re.search(pat, title):
            return label
    return "页面"


def _keywords(title: str, desc: str, given: str) -> list[str]:
    if given and str(given).strip() and str(given) != "nan":
        return [k.strip() for k in re.split(r"[、,，;；|/ ]+", str(given)) if k.strip()]
    text = f"{title} {desc}"
    ks: list[str] = []
    ks += textutil.find_symbols(text) + textutil.find_indicators(text)
    for pat, label in textutil.DATA_SOURCE_RULES + textutil.CHANNEL_RULES:
        if re.search(pat, text):
            ks.append(label.split("（")[0])
    for w in ("日报", "周报", "报表", "页面", "看板", "提醒", "推送", "接口", "流程", "审批", "客户", "产品", "合约", "主力合约", "夜盘", "结算", "权限", "脱敏", "导出", "Excel", "手机"):
        if w in text:
            ks.append(w)
    out: list[str] = []
    for k in ks:
        if k not in out:
            out.append(k)
    return out[:10]


def load_table(path: Path, sheet: str | None) -> pd.DataFrame:
    if path.suffix.lower() in (".csv", ".tsv"):
        return pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else ",", encoding="utf-8-sig")
    return pd.read_excel(path, sheet_name=sheet or 0, header=None)


def _with_header(df: pd.DataFrame) -> pd.DataFrame:
    """模板第 1 行是说明、第 2 行是表头；原始导出第 1 行就是表头。自动判断。"""
    for i in range(min(5, len(df))):
        row = df.iloc[i].tolist()
        if sum(1 for x in row if isinstance(x, str) and x.strip()) >= 3:
            out = df.iloc[i + 1:].copy()
            out.columns = [str(c).strip() for c in row]
            return out.dropna(how="all")
    df.columns = df.iloc[0].tolist()
    return df.iloc[1:].dropna(how="all")


def rows_to_history(df: pd.DataFrame, prefix: str = "H") -> list[dict]:
    df = _with_header(df) if df.columns.dtype != object or all(isinstance(c, int) for c in df.columns) else df
    c = {k: _col(df, k) for k in ALIASES}
    if not c["title"]:
        raise SystemExit(f"找不到标题列，现有列：{list(df.columns)}")
    items = []
    for i, r in enumerate(df.to_dict("records"), 1):
        title = str(r.get(c["title"], "")).strip()
        if not title or title == "nan" or title.startswith("说明"):
            continue
        est = _days(r.get(c["estimate"])) if c["estimate"] else None
        act = _days(r.get(c["actual"])) if c["actual"] else None
        if act is None:
            continue   # 没有实际人天的记录对校准没用
        ai = str(r.get(c["ai"], "")).strip() if c["ai"] else ""
        share = r.get(c["ai_share"]) if c["ai_share"] else None
        items.append({
            "id": str(r.get(c["id"], "")).strip() if c["id"] and str(r.get(c["id"], "")).strip() not in ("", "nan") else f"{prefix}{i:03d}",
            "year": _year(r.get(c["year"], "")) if c["year"] else 0,
            "title": title,
            "type": _type(title, r.get(c["type"], "") if c["type"] else ""),
            "dept": str(r.get(c["dept"], "")).strip() if c["dept"] else "",
            "keywords": _keywords(title, str(r.get(c["desc"], "")) if c["desc"] else "", r.get(c["keywords"], "") if c["keywords"] else ""),
            "estimate_days": est if est is not None else act,
            "actual_days": act,
            "note": str(r.get(c["note"], "")).strip() if c["note"] and str(r.get(c["note"], "")) != "nan" else "",
            "ai_assisted": ai in ("是", "Y", "y", "true", "True", "1"),
            "ai_share": float(share) if share not in (None, "", "nan") and not (isinstance(share, float) and pd.isna(share)) else 0.0,
        })
    return items


def rows_to_probes(df: pd.DataFrame) -> list[dict]:
    df = _with_header(df)
    out = []
    for i, r in enumerate(df.to_dict("records"), 1):
        r = {str(k).strip(): v for k, v in r.items()}
        q = str(r.get("问题", "")).strip()
        if not q or q == "nan":
            continue
        trig = [t.strip() for t in re.split(r"\|", str(r.get("触发词（用 | 分隔，支持正则）", r.get("触发词", "")))) if t.strip()]
        out.append({"id": str(r.get("编号", f"U{i:02d}")).strip(), "category": str(r.get("类别", "补充")).strip(), "tag": str(r.get("标签（期货/通用）", r.get("标签", "期货"))).strip(),
                    "impact": str(r.get("影响（高/中/低）", r.get("影响", "中"))).strip(), "triggers": trig or [".*"], "question": q,
                    "why": str(r.get("不问会怎样", "")).strip(), "default": str(r.get("默认假设", "")).strip(), "source": str(r.get("来源（故障编号/规范条款/同事）", r.get("来源", ""))).strip()})
    return out


def rows_to_catalog(df: pd.DataFrame) -> list[dict]:
    df = _with_header(df)
    out = []
    for r in df.to_dict("records"):
        r = {str(k).strip(): v for k, v in r.items()}
        name = str(r.get("系统 / 组件名", r.get("系统名", ""))).strip()
        if not name or name == "nan":
            continue
        caps = [c.strip() for c in re.split(r"[、,，;；|/ ]+", str(r.get("能力关键词（顿号分隔）", r.get("能力关键词", "")))) if c.strip()]
        out.append({"id": str(r.get("编号", name[:3])).strip(), "name": name, "owner": str(r.get("负责团队", "")).strip(), "capabilities": caps,
                    "integration": str(r.get("接入方式", "")).strip(), "maturity": str(r.get("成熟度（成熟/试点/改动受控）", r.get("成熟度", "成熟"))).strip()})
    return out


def write_json(path: Path, key: str, items: list[dict], note: str, dry: bool) -> None:
    print(f"→ {path.name}：{len(items)} 条")
    for it in items[:3]:
        print("   ", json.dumps(it, ensure_ascii=False)[:160])
    if dry:
        return
    if path.exists():
        shutil.copy(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text(json.dumps({"_说明": note, key: items}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   已写入（原文件备份为 {path.name}.bak）")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["template", "tickets"])
    ap.add_argument("file")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    path = Path(a.file)
    if a.mode == "tickets":
        df = load_table(path, a.sheet)
        items = rows_to_history(df, prefix="W")
        write_json(config.HISTORY_PATH, "items", items, "历史需求库：来自公司工单系统导出（本机导入，已去除敏感信息）。ai_assisted / ai_share 为 AI 协同标记。", a.dry_run)
        return
    xl = pd.ExcelFile(path)
    if "历史需求" in xl.sheet_names:
        items = rows_to_history(pd.read_excel(path, sheet_name="历史需求", header=None))
        write_json(config.HISTORY_PATH, "items", items, "历史需求库：来自公司真实需求记录（本机导入，已去除敏感信息）。", a.dry_run)
    if "追问规则" in xl.sheet_names:
        probes = rows_to_probes(pd.read_excel(path, sheet_name="追问规则", header=None))
        if probes:
            old = json.loads(config.PROBES_PATH.read_text(encoding="utf-8"))["probes"]
            ids = {p["id"] for p in probes}
            merged = [p for p in old if p["id"] not in ids] + probes
            write_json(config.PROBES_PATH, "probes", merged, "期货 IT 需求追问知识库：示例规则 + 公司自建规则（本机导入）。", a.dry_run)
    if "系统目录" in xl.sheet_names:
        systems = rows_to_catalog(pd.read_excel(path, sheet_name="系统目录", header=None))
        if systems:
            write_json(config.CATALOG_PATH, "systems", systems, "公司系统 / 组件目录（本机导入）。", a.dry_run)


if __name__ == "__main__":
    main()

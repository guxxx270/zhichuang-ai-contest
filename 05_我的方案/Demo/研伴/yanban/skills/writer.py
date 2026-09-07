"""代笔：周报 / 投委会材料初稿 = 模板 + 本周晨读要点 + 数据 + 合规自检。人改定签发，研伴不替人拍板。"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .. import config
from ..llm import LLM
from ..profile import Profile
from .morning import Brief


class Finding(BaseModel):
    kind: str          # 禁用表述 / 缺失要素 / 需核对
    term: str
    suggestion: str
    severity: str = "中"


def load_template(name: Optional[str] = None) -> str:
    return (config.TEMPLATES_DIR / (name or "weekly_report.md")).read_text(encoding="utf-8")


def weekly_report(profile: Profile, brief: Optional[Brief], notes: str = "", template: Optional[str] = None,
                  as_of: Optional[date] = None, llm: Optional[LLM] = None) -> tuple[str, str]:
    """返回 (初稿 markdown, 引擎)。mock：模板填充；api：模型在模板与要点范围内润色。"""
    llm = llm or LLM()
    as_of = as_of or (brief.as_of if brief else date.today())
    tpl = template or load_template(profile.weekly_template)
    review, focus, data, events = [], [], [], []
    if brief:
        for s in brief.sections:
            stances = [p for p in s.points if p.stance != "未明确"]
            if stances:
                review.append(f"**{s.name}**：" + "；".join(f"{p.publisher}{p.stance}（{p.summary[:40]}）" for p in stances[:3]))
            if s.divergence:
                focus.append(f"{s.name}：{s.divergence}")
            data += [f"{s.name}：{x}" for x in s.data_points[:2]]
            events += [f"{e.date.strftime('%m-%d')} {s.name}：{e.event}" for e in s.events[:2]]
    fill = {
        "date": as_of.isoformat(), "author": profile.name, "department": profile.department,
        "watch": "、".join(profile.watch_names()),
        "review": "\n".join(f"- {x}" for x in review) or "- （本周暂无要点，先生成晨读）",
        "focus": "\n".join(f"- {x}" for x in focus) or "- 本周各家观点较为一致",
        "data": "\n".join(f"- {x}" for x in data) or "- （待补充）",
        "events": "\n".join(f"- {x}" for x in events) or "- （待补充）",
        "notes": notes.strip() or "（无）",
    }
    draft = tpl
    for k, v in fill.items():
        draft = draft.replace("{{" + k + "}}", v)
    engine = "rule"
    if llm.mode == "api":
        try:
            polished = llm.chat("你是期货公司研究所的周报编辑研伴。在不新增任何判断、不改动数字与来源的前提下，把草稿润色为通顺的周报，"
                                "保留所有标题与免责声明，使用 Markdown。", draft)
            if polished.strip():
                draft, engine = polished, "llm"
        except Exception:
            pass
    return draft, engine


def compliance_check(text: str, terms_path: Optional[Path] = None) -> list[Finding]:
    cfg = json.loads(Path(terms_path or config.COMPLIANCE_TERMS).read_text(encoding="utf-8"))
    out: list[Finding] = []
    for item in cfg["forbidden"]:
        for m in re.finditer(item["term"], text):
            out.append(Finding(kind="禁用表述", term=m.group(0), suggestion=item["suggestion"], severity=item.get("severity", "高")))
    for req in cfg["required_sections"]:
        if not re.search(req["pattern"], text):
            out.append(Finding(kind="缺失要素", term=req["name"], suggestion=req["suggestion"], severity="中"))
    for m in re.finditer(r"(?<![\d.])\d{1,3}(?:\.\d+)?%(?![\d])", text):
        ctx = text[max(0, m.start() - 12): m.end()]
        if not re.search(r"来源|据|显示|数据|口径", ctx):
            out.append(Finding(kind="需核对", term=m.group(0), suggestion="百分比数字建议注明来源或口径", severity="低"))
            if len(out) > 40:
                break
    return out

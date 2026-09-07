"""晨读：把昨夜到今晨的材料按「我的关注」做成 5 分钟速读——各家怎么说、哪里有分歧、数据变了什么、今天有什么事件；可追问。

设计原则：
- 分歧只做「谁和谁说法不同、分歧在哪」，不做对错判断，不做准确率。
- 宁短勿错：每条要点都带来源；LLM 输出解析失败自动退回规则版。
"""
from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .. import config
from ..docs import Doc
from ..llm import LLM
from ..profile import Profile
from ..symbols import SECTORS, name_of

Stance = Literal["偏多", "偏空", "中性", "未明确"]

_STANCE_KW = [
    ("偏多", ["看多", "偏多", "看涨", "上涨", "逢低做多", "逢低买入", "偏强", "做多", "反弹", "走强", "上行", "有望走强"]),
    ("偏空", ["看空", "偏空", "看跌", "下跌", "逢高做空", "逢高沽空", "偏弱", "做空", "回落", "走弱", "下行", "承压", "下行空间"]),
    ("中性", ["震荡", "区间波动", "观望", "盘整", "横盘", "方向不明", "中性"]),
]
_NUM_SENT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|万吨|吨|万手|手|亿|万元|元|美元|个百分点|周|天)")
_NEG = ["风险提示", "不构成", "免责"]


class Point(BaseModel):
    doc_id: str
    title: str
    publisher: str
    doc_type: str
    published_on: date
    symbol: str
    stance: Stance = "未明确"
    summary: str = ""
    reason: str = ""
    data_points: list[str] = Field(default_factory=list)
    quote: str = ""


class Event(BaseModel):
    date: date
    symbol: str          # 品种代码或 ALL
    event: str
    source: str = ""


class Section(BaseModel):
    symbol: str
    name: str
    sector: str
    points: list[Point]
    divergence: Optional[str] = None
    data_points: list[str] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)


class Brief(BaseModel):
    as_of: date
    profile_name: str
    watch: list[str]
    sections: list[Section]
    doc_count: int
    unread_symbols: list[str] = Field(default_factory=list)     # 材料里有、但不在关注里的品种
    engine: str = "rule"
    minutes_saved: int = 0
    markdown: str = ""


# ---------- 事件日历 ----------
def load_events(path: Optional[Path] = None) -> list[Event]:
    p = Path(path or config.EVENTS_CSV)
    if not p.exists():
        return []
    out = []
    with p.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                out.append(Event(date=date.fromisoformat(row["date"]), symbol=row["symbol"].strip().upper(),
                                 event=row["event"].strip(), source=row.get("source", "").strip()))
            except Exception:
                continue
    return out


# ---------- 要点抽取 ----------
def _stance_of(sent: str) -> Stance:
    best: tuple[int, Stance] | None = None
    for st, kws in _STANCE_KW:
        for k in kws:
            pos = sent.rfind(k)
            if pos >= 0 and (best is None or pos > best[0]):
                best = (pos, st)  # type: ignore[assignment]
    return best[1] if best else "未明确"


_NEGATE_BEFORE = re.compile(r"(限制|压制|抑制|难以|不会|未见|不宜|不追|无明显)\S{0,2}$")
_CONCLUSION = ["我们认为", "预计", "建议", "看待", "观点", "维持", "判断", "倾向"]
_PARA_HEAD = re.compile(r"^\s*(?:【[^】]{1,6}】\s*)?([一-龥A-Z]{1,6})[:：]")


def _stance_of_para(sents: list[str]) -> tuple[Stance, str]:
    """段落立场：优先取带结论标记的最后一句；关键词前若有「限制/不追」等否定语则忽略。"""
    best: tuple[int, Stance, str] | None = None      # (优先级, 立场, 句子)
    for i, sent in enumerate(sents):
        hit: tuple[int, Stance] | None = None
        for st, kws in _STANCE_KW:
            for k in kws:
                pos = sent.rfind(k)
                if pos < 0 or _NEGATE_BEFORE.search(sent[max(0, pos - 6):pos]):
                    continue
                if hit is None or pos > hit[0]:
                    hit = (pos, st)  # type: ignore[assignment]
        if hit:
            prio = i + (100 if any(c in sent for c in _CONCLUSION) else 0)
            if best is None or prio >= best[0]:
                best = (prio, hit[1], sent)
    return (best[1], best[2]) if best else ("未明确", "")


def rule_points(doc: Doc, symbols: Optional[list[str]] = None) -> list[Point]:
    """规则版：按段落归属品种（「铁矿石：……」段只归铁矿石），段内取结论句定立场，带数字的句子作数据点。"""
    from ..symbols import SYMBOLS
    wanted = set(symbols) if symbols else set(doc.symbols)
    by_sym: dict[str, dict] = {}
    for para in re.split(r"\n\s*\n|\n(?=\s*(?:【|[一-龥A-Z]{1,6}[:：]))", doc.text):
        para = para.strip()
        if not para or any(n in para for n in _NEG) or para.startswith("（"):
            continue
        sents = [s.strip() for s in re.split(r"[。！？!?\n]+", para) if len(s.strip()) > 3]
        head = _PARA_HEAD.match(para)
        if head and head.group(1) in SYMBOLS:
            targets = {SYMBOLS[head.group(1)]}
        else:
            targets = {SYMBOLS[n] for n in SYMBOLS if n in para}
        targets &= wanted
        if not targets:
            continue
        stance, concl = _stance_of_para(sents)
        data = [s[:100] for s in sents if _NUM_SENT.search(s)][:3]
        for code in targets:
            slot = by_sym.setdefault(code, {"stance": "未明确", "summary": "", "data": [], "quote": ""})
            if stance != "未明确" and (slot["stance"] == "未明确" or not slot["summary"]):
                slot["stance"], slot["summary"], slot["quote"] = stance, concl[:80], concl[:90]
            elif not slot["summary"]:
                slot["summary"] = sents[0][:80] if sents else ""
            for d in data:
                if d not in slot["data"] and len(slot["data"]) < 3:
                    slot["data"].append(d)
    return [Point(doc_id=doc.id, title=doc.title, publisher=doc.publisher, doc_type=doc.doc_type,
                  published_on=doc.published_on, symbol=c, stance=v["stance"], summary=v["summary"],
                  data_points=v["data"], quote=v["quote"]) for c, v in by_sym.items()]


def _prompt(name: str) -> tuple[str, str]:
    md = (config.PROMPT_DIR / name).read_text(encoding="utf-8")
    return md.split("## System", 1)[1].split("## User", 1)[0].strip(), md.split("## User", 1)[1].strip()


def llm_points(doc: Doc, symbols: list[str], llm: LLM) -> list[Point]:
    sys_t, user_t = _prompt("morning_extract.md")
    fill = {"symbols": "、".join(f"{name_of(c)}={c}" for c in symbols), "title": doc.title,
            "publisher": doc.publisher, "date": doc.published_on.isoformat(), "text": doc.text[:10000]}
    for k, v in fill.items():
        sys_t, user_t = sys_t.replace("{{" + k + "}}", v), user_t.replace("{{" + k + "}}", v)
    data = llm.chat_json(sys_t, user_t)
    items = data.get("points", data) if isinstance(data, dict) else data
    out = []
    for it in items or []:
        try:
            it = dict(it)
            if it.get("symbol") not in symbols:
                continue
            out.append(Point(doc_id=doc.id, title=doc.title, publisher=doc.publisher, doc_type=doc.doc_type,
                             published_on=doc.published_on, symbol=it["symbol"], stance=it.get("stance", "未明确"),
                             summary=it.get("summary", "")[:80], reason=it.get("reason", "")[:80],
                             data_points=[str(x)[:80] for x in it.get("data_points", [])][:3],
                             quote=it.get("quote", "")[:90]))
        except Exception:
            continue
    return out


def extract_points(doc: Doc, symbols: list[str], llm: Optional[LLM] = None) -> tuple[list[Point], str]:
    llm = llm or LLM()
    wanted = [s for s in symbols if s in doc.symbols]
    if not wanted:
        return [], "rule"
    if llm.mode == "api":
        try:
            pts = llm_points(doc, wanted, llm)
            if pts:
                return pts, "llm"
        except Exception:
            pass
    return rule_points(doc, wanted), "rule"


# ---------- 组装晨读 ----------
def build_brief(docs: list[Doc], profile: Profile, as_of: Optional[date] = None, llm: Optional[LLM] = None,
                lookback_days: int = 7, events: Optional[list[Event]] = None) -> Brief:
    llm = llm or LLM()
    as_of = as_of or (max(d.published_on for d in docs) if docs else date.today())
    window = [d for d in docs if as_of - timedelta(days=lookback_days) <= d.published_on <= as_of]
    events = events if events is not None else load_events()
    engines = Counter()
    all_points: list[Point] = []
    for d in window:
        pts, eng = extract_points(d, profile.watch, llm)
        engines[eng] += 1
        all_points.extend(pts)
    sections: list[Section] = []
    for code in profile.watch:
        pts = [p for p in all_points if p.symbol == code]
        ev = [e for e in events if e.symbol in (code, "ALL") and as_of <= e.date <= as_of + timedelta(days=14)]
        if not pts and not ev:
            continue
        houses = [p for p in pts if p.doc_type == "研报" and p.stance != "未明确"]   # 分歧只在研报之间统计
        stances = Counter(p.stance for p in houses)
        div = None
        if stances.get("偏多") and stances.get("偏空"):
            bulls = "、".join(p.publisher for p in houses if p.stance == "偏多")
            bears = "、".join(p.publisher for p in houses if p.stance == "偏空")
            div = f"{stances['偏多']} 家偏多（{bulls}） vs {stances['偏空']} 家偏空（{bears}）"
            if stances.get("中性"):
                div += f"，另有 {stances['中性']} 家中性"
        data = []
        for p in pts:
            for x in p.data_points:
                if x not in data:
                    data.append(x)
        sections.append(Section(symbol=code, name=name_of(code), sector=SECTORS.get(code, "其他"), points=pts,
                                divergence=div, data_points=data[:4], events=sorted(ev, key=lambda e: e.date)[:4]))
    unread = sorted({s for d in window for s in d.symbols} - set(profile.watch))
    brief = Brief(as_of=as_of, profile_name=profile.name, watch=profile.watch, sections=sections,
                  doc_count=len(window), unread_symbols=unread,
                  engine="llm" if engines.get("llm") else "rule",
                  minutes_saved=max(5, 3 * len(window) + 5 * len([s for s in sections if s.divergence])))
    brief.markdown = render_brief(brief)
    return brief


def render_brief(b: Brief) -> str:
    lines = [f"### 🌅 {b.profile_name}的晨读 · {b.as_of.isoformat()}",
             f"> 共读 {b.doc_count} 份材料，覆盖你关注的 {len(b.sections)} 个品种；分歧 {sum(1 for s in b.sections if s.divergence)} 处。"]
    for s in b.sections:
        lines.append(f"\n#### {s.name}（{s.sector}）")
        if s.divergence:
            lines.append(f"**⚡ 分歧雷达**：{s.divergence}")
        for p in s.points:
            tag = {"偏多": "🟢", "偏空": "🔴", "中性": "🟡"}.get(p.stance, "⚪")
            lines.append(f"- {tag} **{p.stance}** ｜ {p.summary} ｜ 来源：{p.publisher}《{p.title}》({p.published_on.strftime('%m-%d')})")
        if s.data_points:
            lines.append("- 📊 数据：" + "；".join(s.data_points[:3]))
        for e in s.events:
            lines.append(f"- 📅 {e.date.strftime('%m-%d')} {e.event}" + (f"（{e.source}）" if e.source else ""))
    if b.unread_symbols:
        lines.append(f"\n> 材料里还提到了你未关注的品种：{'、'.join(name_of(c) for c in b.unread_symbols)}。要加入关注吗？")
    lines.append("\n> 研伴只汇总各家说法与公开数据，不做对错判断；内容仅供内部研究参考。")
    return "\n".join(lines)


# ---------- 追问 ----------
def ask(question: str, docs: list[Doc], llm: Optional[LLM] = None, k: int = 4) -> tuple[str, list[tuple[str, str]]]:
    """基于已读材料回答追问。返回 (答案, [(来源, 原句)])。mock：按字重叠找最相关句子；api：把片段交给模型。"""
    llm = llm or LLM()
    q_chars = set(re.findall(r"[一-龥A-Za-z0-9]", question))
    scored: list[tuple[float, str, str]] = []
    for d in docs:
        for s in d.sentences():
            sc = len(q_chars & set(s)) / (len(q_chars) + 1)
            if sc > 0.15:
                scored.append((sc, f"{d.publisher}《{d.title}》", s))
    scored.sort(reverse=True)
    hits = [(src, s) for _, src, s in scored[:k]]
    if not hits:
        return "已读材料里没有找到相关内容。", []
    if llm.mode == "api":
        try:
            ctx = "\n".join(f"[{i+1}] {src}：{s}" for i, (src, s) in enumerate(hits))
            ans = llm.chat("你是投研助手研伴。只根据给定片段回答，逐条标注来源编号；片段不足以回答时明确说不知道；不做投资建议。",
                           f"问题：{question}\n\n片段：\n{ctx}")
            if ans.strip():
                return ans, hits
        except Exception:
            pass
    return "根据已读材料：\n" + "\n".join(f"- {s}（{src}）" for src, s in hits), hits

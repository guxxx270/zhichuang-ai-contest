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
    ("偏多", ["看多", "偏多", "看涨", "上涨", "逢低做多", "逢低买入", "偏强", "做多", "反弹", "走强", "上行", "有望走强",
             "多单持有", "看涨期权持有", "逢低短多", "回落可短多", "易涨难跌", "短多", "多为主", "拉涨", "走高", "向上突破"]),
    ("偏空", ["看空", "偏空", "看跌", "下跌", "逢高做空", "逢高沽空", "偏弱", "做空", "回落", "走弱", "下行", "承压", "下行空间",
             "空单持有", "看跌期权持有", "逢高短空", "易跌难涨", "回调风险", "短空", "空为主", "下探", "向下突破"]),
    ("中性", ["震荡", "区间波动", "观望", "盘整", "横盘", "方向不明", "中性", "多空交织", "多空博弈", "涨跌不一",
             "判断的难度", "难以判断", "方向判断", "等待报告", "等待USDA", "窄幅波动"]),
]
# 「宽幅震荡 / 高位震荡 / 震荡偏强」这类复合词：先按最后出现的关键词定性，再由 _REFINE 修正
_REFINE = [("震荡偏强", "偏多"), ("震荡偏多", "偏多"), ("偏强震荡", "偏多"), ("偏多震荡", "偏多"),
           ("震荡偏弱", "偏空"), ("震荡偏空", "偏空"), ("偏弱震荡", "偏空"), ("偏空震荡", "偏空"),
           ("宽幅震荡", "中性"), ("高位震荡", "中性"), ("低位震荡", "中性"), ("震荡整理", "中性"), ("震荡调整", "中性"), ("震荡运行", "中性")]
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
_NEGATE_BEFORE = re.compile(r"(限制|压制|抑制|难以|不会|未见|不宜|不追|无明显|难见|尚未|缺乏)\S{0,2}$")
_NEGATE_AFTER = re.compile(r"^\S{0,4}?(乏力|有限|受限|不足|难度|放缓|动力不足|空间有限|尚需|存疑)")
_VIEW_LABEL = re.compile(r"(观点|策略|结论|建议)[:：]")
_ANY_LABEL = re.compile(r"^[一-龥A-Za-z]{2,8}[:：]")          # 「装置信息：」「产销：」这类小标题会结束观点块
# 结论句标记：强（策略/观点句）> 弱（预计/短期句）> 段首短标题句 > 普通句
_STRONG = ["策略", "操作", "建议", "我们认为", "观点", "看待", "倾向", "综合来看", "整体来看", "总体来看", "综合而言"]
_WEAK = ["预计", "维持", "判断", "短期", "后市", "展望", "或将", "有望", "料将"]
_CONCLUSION = _STRONG + _WEAK
_PARA_HEAD = re.compile(r"^\s*(?:【[^】]{1,8}】\s*)?([一-龥A-Za-z&]{1,8})[:：]")
_PARA_SPLIT = re.compile(r"\n\s*\n|\n(?=\s*(?:【|[一-龥A-Za-z&]{1,8}[:：]))")


def _sent_stance(sent: str) -> Optional[Stance]:
    """一句话的立场：取最后出现的关键词；「限制反弹」这类否定语前缀忽略；复合词（震荡偏强 / 宽幅震荡）再修正。"""
    hit: tuple[int, Stance] | None = None
    for st, kws in _STANCE_KW:
        for k in kws:
            pos = sent.rfind(k)
            if pos < 0 or _NEGATE_BEFORE.search(sent[max(0, pos - 6):pos]) or _NEGATE_AFTER.match(sent[pos + len(k):pos + len(k) + 8]):
                continue
            if hit is None or pos > hit[0]:
                hit = (pos, st)  # type: ignore[assignment]
    if hit is None:
        return None
    st = hit[1]
    for phrase, fixed in _REFINE:
        if sent.rfind(phrase) >= 0 and sent.rfind(phrase) + len(phrase) >= hit[0]:
            st = fixed  # type: ignore[assignment]
    return st


def _level(sent: str, i: int) -> int:
    """句子的结论强度：3 策略/观点句，2 预计/短期句，1 段首短标题句（「油脂：偏强趋势未改」），0 普通句。"""
    if any(c in sent for c in _STRONG):
        return 3
    if any(c in sent for c in _WEAK):
        return 2
    return 1 if (i == 0 and len(sent) <= 16) else 0


def _focus(sent: str, limit: int = 80) -> str:
    """长句只留结论所在的分句（含前一分句），避免 80 字截断把结论截掉。"""
    if len(sent) <= limit:
        return sent
    clauses = [c for c in re.split(r"[，；,;]", sent) if c.strip()]
    idx = None
    for i, c in enumerate(clauses):
        if _sent_stance(c):
            idx = i
    if idx is None:
        return sent[:limit]
    out = clauses[idx]
    if idx > 0 and len(out) < 30:
        out = clauses[idx - 1] + "，" + out
    return out[:limit]


def _stance_of_para(sents: list[str]) -> tuple[Stance, str, int]:
    """段落立场 → (立场, 结论句, 强度)。取强度最高的句子；同强度取靠后的一句（结论通常在后）。
    「南华观点：」「策略：」之后的整段视为观点块，块内句子都按强 3 级计。"""
    best: tuple[int, int, Stance, str] | None = None
    in_view = False
    for i, sent in enumerate(sents):
        if _VIEW_LABEL.search(sent):
            in_view = True
        elif _ANY_LABEL.match(sent):
            in_view = False
        st = _sent_stance(sent)
        if not st:
            continue
        key = (3 if in_view else _level(sent, i), i)
        if best is None or key >= best[:2]:
            best = (key[0], key[1], st, sent)
    return (best[2], _focus(best[3]), best[0]) if best else ("未明确", "", -1)


def paragraphs(doc: Doc) -> list[tuple[set[str], str, list[str]]]:
    """把材料切成段落并归属品种：[(品种代码集合, 段落原文, 句子列表)]。
    「铁矿石：……」段只归铁矿石；「油脂：……」段按板块别名归豆油/棕榈油，不归里面顺带提到的原油；
    标题不是品种也不是综述词（如「橡胶」「宏观数据」）的段落，只归标题本身能识别的品种；无标题段按提及归属。"""
    from ..symbols import GENERIC_HEADS, SECTOR_ALIASES, SYMBOLS
    out = []
    for para in _PARA_SPLIT.split(doc.text):
        para = para.strip()
        if not para or any(n in para for n in _NEG) or para.startswith("（"):
            continue
        sents = [s.strip() for s in re.split(r"[。！？!?\n]+", para) if len(s.strip()) > 3]
        head = _PARA_HEAD.match(para)
        h = head.group(1) if head else ""
        if h in SYMBOLS:
            targets = {SYMBOLS[h]}
        elif h in SECTOR_ALIASES:
            targets = set(SECTOR_ALIASES[h])
        elif h and h not in GENERIC_HEADS:
            targets = {SYMBOLS[n] for n in SYMBOLS if n in h}       # 「镍&不锈钢」「氧化铝&电解铝」这类组合标题
        else:
            targets = {SYMBOLS[n] for n in SYMBOLS if n in para}
        out.append((targets, para, sents))
    return out


def rule_points(doc: Doc, symbols: Optional[list[str]] = None) -> list[Point]:
    """规则版：按段落归属品种，段内取结论句定立场（同品种多段时取结论强度最高、位置靠后的一段），带数字的句子作数据点。"""
    wanted = set(symbols) if symbols else set(doc.symbols)
    by_sym: dict[str, dict] = {}
    for targets, para, sents in paragraphs(doc):
        overview = len(targets) >= 4 and not _PARA_HEAD.match(para)     # 无标题、点名一堆品种的综述段：只取数据，不定立场
        targets = targets & wanted
        if not targets or not sents:
            continue
        stance, concl, level = ("未明确", "", -1) if overview else _stance_of_para(sents)
        data = [(s if len(s) <= 72 else s[:70] + "…") for s in sents if _NUM_SENT.search(s)][:3]
        for code in targets:
            slot = by_sym.setdefault(code, {"stance": "未明确", "summary": "", "data": [], "quote": "", "level": -1})
            if stance != "未明确" and level >= slot["level"]:
                slot["stance"], slot["summary"], slot["quote"], slot["level"] = stance, concl[:80], concl[:90], level
            elif not slot["summary"]:
                slot["summary"] = sents[0][:80]
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
        if len(stances) >= 2:
            who = {st: "、".join(p.publisher for p in houses if p.stance == st) for st in stances}
            if stances.get("偏多") and stances.get("偏空"):          # 多空对立：分歧雷达
                div = f"{stances['偏多']} 家偏多（{who['偏多']}） vs {stances['偏空']} 家偏空（{who['偏空']}）"
                if stances.get("中性"):
                    div += f"，另有 {stances['中性']} 家中性（{who['中性']}）"
            else:                                                    # 多/空 vs 中性：说法不一
                div = " vs ".join(f"{n} 家{st}（{who[st]}）" for st, n in stances.most_common())
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
    """基于已读材料回答追问。返回 (答案, [(来源, 原句)])。
    mock：问题里提到品种就只在该品种的段落里找；问「看多/看空/理由/为什么/怎么看」时优先结论句；按二字词重叠打分。
    api：把片段交给模型组织语言。"""
    from ..symbols import detect_symbols
    llm = llm or LLM()
    q_syms = set(detect_symbols(question))
    want_stance: Optional[Stance] = "偏多" if re.search(r"看多|看涨|偏多|多头|偏强|走强", question) else \
        ("偏空" if re.search(r"看空|看跌|偏空|空头|偏弱|走弱", question) else None)
    ask_view = bool(re.search(r"理由|为什么|为何|怎么看|观点|逻辑|依据|看法|判断", question)) or want_stance is not None
    q_core = re.sub(r"[看多看空看涨看跌偏多偏空的那家理由是什么为何怎么看观点逻辑依据看法判断哪]", "", question)
    for n in sorted({name_of(c) for c in q_syms}, key=len, reverse=True):
        q_core = q_core.replace(n, "")
    named = [d for d in docs if len(d.publisher) >= 2 and (d.publisher in question or d.publisher[:2] in question)]
    if named:                                                    # 问「光大怎么看」：只在该机构的材料里找
        docs = named
        for d in named:
            q_core = q_core.replace(d.publisher, "").replace(d.publisher[:2], "")
    grams = {q_core[i:i + 2] for i in range(len(q_core) - 1) if re.fullmatch(r"[一-龥A-Za-z0-9]{2}", q_core[i:i + 2])}
    scored: list[tuple[float, str, str]] = []
    for d in docs:
        src = f"{d.publisher}《{d.title}》"
        for targets, _para, sents in paragraphs(d):
            if q_syms and not (targets & q_syms):
                continue
            for i, s in enumerate(sents):
                st = _sent_stance(s)
                if want_stance and st != want_stance:
                    continue
                sc = len({s[j:j + 2] for j in range(len(s) - 1)} & grams) / (len(grams) + 1)
                if ask_view:
                    sc += 0.5 * _level(s, i) + (0.3 if st else 0)
                if q_syms and not grams and not ask_view:
                    sc += 0.1                                  # 只问品种名：全部候选
                if sc > 0.15:
                    scored.append((sc, src, s))
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

"""观点抽取：文本 → 观点卡。api 模式走 LLM，mock 模式走规则；LLM 输出异常时也用规则兜底。"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Optional

from . import config
from .llm import LLM
from .schema import SYMBOL_NAMES, SYMBOLS, OpinionCard

_DIR_KW = [
    ("多", ["看多", "偏多", "看涨", "上涨", "逢低做多", "逢低买入", "偏强", "做多", "反弹", "走强", "上行"]),
    ("空", ["看空", "偏空", "看跌", "下跌", "逢高做空", "逢高沽空", "偏弱", "做空", "回落", "走弱", "下行", "承压"]),
    ("震荡", ["震荡", "区间波动", "观望", "盘整", "横盘", "无明显方向", "方向不明"]),
]
_HORIZON_KW = [(60, ["季度", "中长期", "三个月", "长期"]), (20, ["中期", "一个月", "月内", "月度"]),
               (10, ["两周", "半月"]), (5, ["短期", "一周", "本周", "周内", "日内"])]
_CONF_KW = [(0.85, ["强烈", "确定性高", "坚定", "明确"]), (0.4, ["可能", "或将", "不排除", "存在"])]
_DATE_RE = re.compile(r"(20\d{2})[-/年.](\d{1,2})[-/月.](\d{1,2})日?")
_ANALYST_RE = re.compile(r"(?:分析师|研究员|投资经理|作者|撰稿)[:：]\s*([一-龥A-Za-z·]{2,6})")
_SEG_ANALYST_RE = re.compile(r"^\s*[【\[]?([一-龥]{2,4})[】\]]?[:：]\s*(.+)$")
_ROLE_WORDS = {"分析师", "研究员", "投资经理", "作者", "撰稿", "记录", "日期", "来源", "主讲", "发言"}
_ENUM_RE = re.compile(r"^[一二三四五六七八九十\d]+[、.．]\s*")
_NEG_KW = ["不给出", "暂不", "风险提示", "不构成", "若", "如果", "需重新评估", "重点跟踪", "会议决定"]


def _direction_of(sent: str) -> Optional[str]:
    best: tuple[int, str] | None = None
    for d, kws in _DIR_KW:
        for k in kws:
            pos = sent.rfind(k)
            if pos >= 0 and (best is None or pos > best[0]):
                best = (pos, d)
    return best[1] if best else None


def _load_prompt() -> str:
    return (config.PROMPT_DIR / "extract_opinion.md").read_text(encoding="utf-8")


def _split_prompt(md: str) -> tuple[str, str]:
    sys_part = md.split("## System", 1)[1].split("## User", 1)[0].strip()
    user_part = md.split("## User", 1)[1].strip()
    return sys_part, user_part


def guess_date(text: str, fallback: Optional[date] = None) -> date:
    m = _DATE_RE.search(text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            pass
    return fallback or date.today()


def guess_analyst(text: str, fallback: str = "未署名") -> str:
    m = _ANALYST_RE.search(text)
    return m.group(1) if m else fallback


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[。！？!?\n]+", text) if s.strip()]


def rule_extract(text: str, source: str = "", default_analyst: Optional[str] = None,
                 published_on: Optional[date] = None) -> list[OpinionCard]:
    """规则抽取：按句扫描品种 + 方向词。mock 模式主力，api 模式兜底。"""
    pub = published_on or guess_date(text)
    doc_analyst = default_analyst or guess_analyst(text)
    cards: dict[str, OpinionCard] = {}
    current_analyst = doc_analyst
    for sent in _sentences(text):
        m = _SEG_ANALYST_RE.match(sent)
        if m and m.group(1) not in SYMBOLS:
            label, rest = m.group(1), m.group(2)
            if label in _ROLE_WORDS:               # 「分析师：张研」→ 全文默认作者
                name = re.match(r"[一-龥A-Za-z·]{2,6}", rest.strip())
                if name and label not in ("记录", "日期", "来源", "撰稿"):
                    current_analyst = doc_analyst = name.group(0)
                continue
            current_analyst, sent = label, rest   # 「李铜：……」的纪要发言段
        sent = _ENUM_RE.sub("", sent)
        if any(k in sent for k in _NEG_KW):      # 「暂不给出方向性判断」「风险提示」之类不登记
            continue
        found_syms = [(name, code) for name, code in SYMBOLS.items() if name in sent]
        if not found_syms:
            continue
        head = re.match(r"^\s*([一-龥A-Z]{1,6})[:：]", sent)   # 「豆油：……」只归属句首品种
        if head and head.group(1) in SYMBOLS:
            found_syms = [(head.group(1), SYMBOLS[head.group(1)])]
        direction = _direction_of(sent)          # 取句中最后出现的方向词（结论通常在句尾）
        if not direction:
            continue
        horizon = next((h for h, kws in _HORIZON_KW if any(k in sent for k in kws)), 20)
        conf = next((c for c, kws in _CONF_KW if any(k in sent for k in kws)), 0.6)
        # 同一句多个品种名可能是别名重叠（铜/沪铜），按代码去重，取最长名
        by_code: dict[str, str] = {}
        for name, code in found_syms:
            if code not in by_code or len(name) > len(by_code[code]):
                by_code[code] = name
        for code, name in by_code.items():
            card = OpinionCard(analyst=current_analyst, symbol=code, symbol_name=SYMBOL_NAMES.get(code, name),
                               direction=direction, horizon_days=horizon, confidence=conf,
                               rationale=sent[:60], published_on=pub, source=source, source_quote=sent[:80])
            cards.setdefault(card.key(), card)
    return list(cards.values())


def llm_extract(llm: LLM, text: str, source: str, default_analyst: str, published_on: date) -> list[OpinionCard]:
    sys_t, user_t = _split_prompt(_load_prompt())
    symbol_hint = "、".join(f"{n}={c}" for c, n in SYMBOL_NAMES.items())
    fill = {"default_analyst": default_analyst, "symbol_hint": symbol_hint,
            "published_on": published_on.isoformat(), "source": source, "text": text[:12000]}
    for k, v in fill.items():
        sys_t, user_t = sys_t.replace("{{" + k + "}}", str(v)), user_t.replace("{{" + k + "}}", str(v))
    data = llm.chat_json(sys_t, user_t)
    items = data.get("cards", data) if isinstance(data, dict) else data
    cards: list[OpinionCard] = []
    for it in items or []:
        try:
            it = dict(it)
            it.setdefault("published_on", published_on.isoformat())
            it.setdefault("analyst", default_analyst)
            it["source"] = source
            if not it.get("symbol") and it.get("symbol_name"):
                it["symbol"] = SYMBOLS.get(it["symbol_name"], "")
            if not it.get("symbol"):
                continue
            it.setdefault("symbol_name", SYMBOL_NAMES.get(it["symbol"], ""))
            cards.append(OpinionCard(**it))
        except Exception:
            continue
    return cards


def extract_opinions(text: str, source: str = "", default_analyst: Optional[str] = None,
                     published_on: Optional[date] = None, llm: Optional[LLM] = None) -> tuple[list[OpinionCard], str]:
    """返回 (观点卡列表, 使用的引擎)。"""
    llm = llm or LLM()
    pub = published_on or guess_date(text)
    analyst = default_analyst or guess_analyst(text)
    if llm.mode == "api":
        try:
            cards = llm_extract(llm, text, source, analyst, pub)
            if cards:
                return cards, "llm"
        except Exception:
            pass
    return rule_extract(text, source, analyst, pub), "rule"


def read_text_file(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            return "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        except ImportError as e:
            raise RuntimeError("读取 PDF 需要 pypdf：pip install pypdf") from e
    return path.read_text(encoding="utf-8", errors="ignore")

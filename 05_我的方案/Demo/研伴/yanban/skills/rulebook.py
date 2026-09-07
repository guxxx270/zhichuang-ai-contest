"""问典：品种规则与制度的问答 + 计算——涨跌停、保证金、限仓校验、最后交易日、交割；每个数字带出处。

数据：data/rules/params.json（品种参数，示例值，以交易所最新公告为准）+ data/rules/*.md（规则条款摘录，每条带【出处】）。
原则：先算后说、先确认口径再算（NL2DSL 的"二次确认"思路）；模型只负责组织语言，不负责算数。
"""
from __future__ import annotations

import calendar
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .. import config
from ..llm import LLM
from ..symbols import SYMBOLS, name_of

_CONTRACT = re.compile(r"(?<!\d)(\d{2})(0[1-9]|1[0-2])(?!\d)")          # 2609
_PRICE = re.compile(r"(?:结算价|价格|价|按)\s*(\d{2,7}(?:\.\d+)?)")
_LOTS = re.compile(r"(\d{1,7})\s*手")
_STAGE_KW = [("交割月前一月", ["前一月", "前一个月", "临近交割"]), ("交割月", ["交割月", "当月"]), ("一般月份", [])]
_INTENT_HINT = {"涨跌停计算": "涨跌停板", "保证金计算": "保证金", "限仓校验": "限仓", "最后交易日": "最后交易日"}


class Clause(BaseModel):
    title: str
    text: str
    source: str
    symbols: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    question: str
    intent: str
    text: str
    citations: list[str] = Field(default_factory=list)
    calc: dict = Field(default_factory=dict)
    engine: str = "rule"


class RuleBook:
    def __init__(self, rules_dir: Optional[Path] = None) -> None:
        d = Path(rules_dir or config.RULES_DIR)
        self.params: dict[str, dict] = json.loads((d / "params.json").read_text(encoding="utf-8"))
        self.clauses: list[Clause] = []
        for p in sorted(d.glob("*.md")):
            self.clauses.extend(self._parse(p.read_text(encoding="utf-8")))

    @staticmethod
    def _parse(md: str) -> list[Clause]:
        out = []
        for block in re.split(r"\n(?=### )", md):
            if not block.startswith("### "):
                continue
            title, _, body = block[4:].partition("\n")
            src = re.search(r"【出处】\s*(.+)", body)
            text = re.sub(r"【出处】.*", "", body).strip()
            out.append(Clause(title=title.strip(), text=text, source=src.group(1).strip() if src else "未标注出处",
                              symbols=[SYMBOLS[n] for n in SYMBOLS if n in title + text]))
        return out

    # ---------- 检索 ----------
    def search(self, query: str, k: int = 3, symbol: Optional[str] = None) -> list[Clause]:
        q = set(re.findall(r"[一-龥A-Za-z0-9]", query))
        bigrams = {query[i:i + 2] for i in range(len(query) - 1)}
        scored = []
        for c in self.clauses:
            txt = c.title + c.text
            sc = len(q & set(txt)) / (len(q) + 1) + 2 * sum(1 for b in bigrams if b in txt) / (len(bigrams) + 1)
            if symbol and symbol in c.symbols:
                sc += 0.5
            if sc > 0.2:
                scored.append((sc, c))
        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[:k]]

    # ---------- 计算器 ----------
    def _p(self, symbol: str) -> dict:
        p = self.params.get(symbol.upper())
        if not p:
            raise KeyError(f"暂无 {symbol} 的参数，请先在 params.json 登记")
        return p

    def limits(self, symbol: str, settle: float) -> dict:
        p = self._p(symbol)
        pct = p["limit_pct"]
        tick = p["tick"]
        up = round(round(settle * (1 + pct) / tick) * tick, 2)
        down = round(round(settle * (1 - pct) / tick) * tick, 2)
        return {"品种": name_of(symbol), "结算价": settle, "涨跌停幅度": f"{pct:.0%}", "涨停价": up, "跌停价": down,
                "出处": p["source"]}

    def margin(self, symbol: str, price: float, lots: int) -> dict:
        p = self._p(symbol)
        value = price * p["multiplier"] * lots
        need = value * p["margin_pct"]
        return {"品种": name_of(symbol), "价格": price, "手数": lots, "合约乘数": f"{p['multiplier']}{p['unit']}/手",
                "合约价值": round(value, 2), "保证金比例": f"{p['margin_pct']:.0%}", "所需保证金": round(need, 2), "出处": p["source"]}

    def position_check(self, symbol: str, lots: int, stage: str = "一般月份") -> dict:
        p = self._p(symbol)
        limit = p["position_limit"].get(stage) or p["position_limit"]["一般月份"]
        return {"品种": name_of(symbol), "阶段": stage, "持仓": lots, "限仓": limit, "结论": "超限" if lots > limit else "未超限",
                "余量": limit - lots, "出处": p["source"]}

    def last_trading_day(self, symbol: str, contract: str) -> dict:
        p = self._p(symbol)
        yy, mm = int(contract[:2]), int(contract[2:])
        year = 2000 + yy
        rule = p["last_trading_day_rule"]
        if rule["type"] == "day_of_month":
            d = date(year, mm, min(rule["day"], calendar.monthrange(year, mm)[1]))
            while d.weekday() >= 5:
                d += timedelta(days=1)
            note = f"合约月份第 {rule['day']} 日，遇周末顺延（法定假日以交易所公告为准）"
        elif rule["type"] == "nth_trading_day_before_month_end":
            n = rule["n"]
            d = date(year, mm, calendar.monthrange(year, mm)[1])
            cnt = 0
            while True:
                if d.weekday() < 5:
                    cnt += 1
                    if cnt == n:
                        break
                d -= timedelta(days=1)
            note = f"合约月份倒数第 {n} 个交易日（按周末粗算，法定假日以交易所公告为准）"
        else:
            d, note = None, rule.get("text", "")
        return {"品种": name_of(symbol), "合约": f"{symbol.upper()}{contract}", "最后交易日": d.isoformat() if d else "见规则",
                "规则": note, "交割": p.get("delivery", ""), "出处": p["source"]}

    # ---------- 问答入口 ----------
    def answer(self, question: str, llm: Optional[LLM] = None) -> Answer:
        llm = llm or LLM()
        symbol = next((SYMBOLS[n] for n in sorted(SYMBOLS, key=len, reverse=True) if n in question), None)
        cm = _CONTRACT.search(question)
        pm = _PRICE.search(question)
        lm = _LOTS.search(question)
        calc: dict = {}
        intent = "规则问答"
        try:
            if symbol and ("涨跌停" in question or "涨停" in question or "跌停" in question) and pm:
                intent, calc = "涨跌停计算", self.limits(symbol, float(pm.group(1)))
            elif symbol and "保证金" in question and pm and lm:
                intent, calc = "保证金计算", self.margin(symbol, float(pm.group(1)), int(lm.group(1)))
            elif symbol and ("限仓" in question or "超限" in question or "持仓限额" in question) and lm:
                stage = next((s for s, kws in _STAGE_KW if any(k in question for k in kws)), "一般月份")
                intent, calc = "限仓校验", self.position_check(symbol, int(lm.group(1)), stage)
            elif symbol and ("最后交易日" in question or "交割日" in question) and cm:
                intent, calc = "最后交易日", self.last_trading_day(symbol, cm.group(1) + cm.group(2))
        except KeyError as e:
            return Answer(question=question, intent=intent, text=str(e))
        hint = _INTENT_HINT.get(intent, "")
        clauses = self.search((hint + " ") * 3 + question, symbol=symbol)
        if hint:
            clauses.sort(key=lambda c: 0 if hint in c.title else 1)
        cites = [f"{c.title} —— {c.source}" for c in clauses]
        if calc:
            body = "；".join(f"{k}：{v}" for k, v in calc.items() if k != "出处")
            text = f"【{intent}】{body}。\n参数出处：{calc['出处']}。"
            if clauses:
                text += "\n相关规则：" + clauses[0].text[:120] + f"（{clauses[0].source}）"
        elif clauses:
            text = "\n".join(f"- {c.title}：{c.text[:160]}（出处：{c.source}）" for c in clauses)
        else:
            text = "规则库里没有找到相关条款。可以换个说法，或把交易所公告放进 data/rules/。"
        engine = "rule"
        if llm.mode == "api" and clauses:
            try:
                ctx = "\n".join(f"[{i+1}] {c.title}：{c.text}（出处：{c.source}）" for i, c in enumerate(clauses))
                calc_txt = ("计算结果（已由程序算好，不得改动数字）：" + json.dumps(calc, ensure_ascii=False)) if calc else ""
                polished = llm.chat("你是期货公司研究所的规则助手研伴。只依据给定条款与计算结果作答，每个数字和结论后标注来源编号；"
                                    "不确定就说明需人工核对；不做投资建议；不超过 200 字。",
                                    f"问题：{question}\n{calc_txt}\n条款：\n{ctx}")
                if polished.strip():
                    text, engine = polished, "llm"
            except Exception:
                pass
        return Answer(question=question, intent=intent, text=text, citations=cites, calc=calc, engine=engine)

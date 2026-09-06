"""回溯：观点卡 + 行情 → 对/错/收益。规则简单透明，方便评委追问。"""
from __future__ import annotations

from typing import Iterable

from .market import PriceBook
from .schema import OpinionCard, Outcome

HIT_THRESHOLD = 0.01       # 多/空：涨跌幅超过 ±1% 才算"说对"
RANGE_THRESHOLD = 0.02     # 震荡：期间 |涨跌幅| ≤ 2% 算"说对"


def evaluate(card: OpinionCard, prices: PriceBook) -> Outcome:
    start = prices.price_on_or_after(card.symbol, card.published_on)
    if not start:
        return Outcome(card=card, note="无该品种行情或发布日之后无数据")
    end = prices.price_after_n_trading_days(card.symbol, card.published_on, card.horizon_days)
    if not end:
        return Outcome(card=card, start_date=start[0], start_price=start[1], note="未到期")
    pct = end[1] / start[1] - 1
    if card.direction == "多":
        hit, pnl = pct > HIT_THRESHOLD, pct
    elif card.direction == "空":
        hit, pnl = pct < -HIT_THRESHOLD, -pct
    else:
        hit, pnl = abs(pct) <= RANGE_THRESHOLD, (0.0 if abs(pct) <= RANGE_THRESHOLD else -abs(pct))
    return Outcome(card=card, start_date=start[0], end_date=end[0], start_price=start[1], end_price=end[1],
                   pct=round(pct, 4), hit=hit, pnl=round(pnl, 4))


def evaluate_all(cards: Iterable[OpinionCard], prices: PriceBook) -> list[Outcome]:
    return [evaluate(c, prices) for c in cards]

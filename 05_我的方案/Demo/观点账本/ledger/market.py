"""行情数据：默认读 data/sample_prices.csv（date,symbol,close）；可选从 akshare 拉取真实日线。"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from . import config


class PriceBook:
    def __init__(self, df: pd.DataFrame) -> None:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["symbol"] = df["symbol"].astype(str).str.upper()
        self.df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
        self._by_symbol = {s: g.set_index("date")["close"] for s, g in self.df.groupby("symbol")}

    @classmethod
    def from_csv(cls, path: Optional[Path] = None) -> "PriceBook":
        return cls(pd.read_csv(path or (config.DATA_DIR / "sample_prices.csv")))

    def symbols(self) -> list[str]:
        return sorted(self._by_symbol)

    def last_date(self, symbol: str) -> Optional[date]:
        s = self._by_symbol.get(symbol.upper())
        return None if s is None or s.empty else s.index[-1]

    def price_on_or_after(self, symbol: str, d: date) -> Optional[tuple[date, float]]:
        s = self._by_symbol.get(symbol.upper())
        if s is None:
            return None
        idx = s.index.searchsorted(d)
        if idx >= len(s):
            return None
        return s.index[idx], float(s.iloc[idx])

    def price_after_n_trading_days(self, symbol: str, start: date, n: int) -> Optional[tuple[date, float]]:
        s = self._by_symbol.get(symbol.upper())
        if s is None:
            return None
        idx = s.index.searchsorted(start) + n
        if idx >= len(s):
            return None
        return s.index[idx], float(s.iloc[idx])


def fetch_akshare(symbols: list[str], start: date, end: date) -> Optional[pd.DataFrame]:
    """可选：用 akshare 拉主力连续合约日线（新浪源，符号如 RB0 / CU0）。失败返回 None，不影响 Demo。"""
    try:
        import akshare as ak  # type: ignore
    except ImportError:
        return None
    frames = []
    for sym in symbols:
        try:
            df = ak.futures_zh_daily_sina(symbol=f"{sym}0")
            df = df.rename(columns={"date": "date", "close": "close"})[["date", "close"]]
            df["symbol"] = sym
            frames.append(df)
        except Exception:
            continue
    if not frames:
        return None
    out = pd.concat(frames)
    out["date"] = pd.to_datetime(out["date"]).dt.date
    return out[(out["date"] >= start) & (out["date"] <= end)].reset_index(drop=True)


def make_sample_prices(start: date, end: date, symbols: dict[str, float], seed: int = 7) -> pd.DataFrame:
    """生成示例行情（随机游走，仅供 Demo；正式演示请替换为真实数据）。"""
    import random
    rng = random.Random(seed)
    rows = []
    for sym, p0 in symbols.items():
        p = p0
        d = start
        while d <= end:
            if d.weekday() < 5:
                p *= 1 + rng.gauss(0.0004, 0.012)
                rows.append({"date": d.isoformat(), "symbol": sym, "close": round(p, 2)})
            d += timedelta(days=1)
    return pd.DataFrame(rows)

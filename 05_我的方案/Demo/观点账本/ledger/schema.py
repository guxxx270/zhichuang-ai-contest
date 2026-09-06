"""观点卡数据模型：一条研判 = 一张观点卡。"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

Direction = Literal["多", "空", "震荡"]

# 品种词典：中文名 → 代码。抽取与回溯都用它对齐。
SYMBOLS: dict[str, str] = {
    "螺纹钢": "RB", "螺纹": "RB",
    "热卷": "HC", "热轧卷板": "HC",
    "铁矿石": "I", "铁矿": "I",
    "焦炭": "J", "焦煤": "JM",
    "沪铜": "CU", "铜": "CU",
    "沪铝": "AL", "铝": "AL",
    "沪镍": "NI", "镍": "NI",
    "黄金": "AU", "沪金": "AU",
    "白银": "AG", "沪银": "AG",
    "原油": "SC", "SC原油": "SC",
    "豆粕": "M", "豆油": "Y", "棕榈油": "P",
    "玉米": "C", "生猪": "LH",
    "PTA": "TA", "甲醇": "MA", "乙二醇": "EG",
    "沪深300": "IF", "中证500": "IC", "中证1000": "IM",
    "十年期国债": "T", "国债": "T",
}
SYMBOL_NAMES: dict[str, str] = {}
for _n, _c in SYMBOLS.items():
    SYMBOL_NAMES.setdefault(_c, _n)


class OpinionCard(BaseModel):
    """一条结构化研判。"""

    analyst: str = Field(description="研究员/投资经理姓名或代号")
    symbol: str = Field(description="品种代码，如 RB / CU / M / SC")
    symbol_name: str = Field(default="", description="品种中文名")
    direction: Direction = Field(description="多 / 空 / 震荡")
    horizon_days: int = Field(default=20, ge=1, le=250, description="观点期限（交易日）")
    confidence: float = Field(default=0.6, ge=0.0, le=1.0, description="置信度 0～1")
    rationale: str = Field(default="", description="核心逻辑，不超过 60 字")
    published_on: date = Field(description="观点发布日期")
    source: str = Field(default="", description="来源文件")
    source_quote: str = Field(default="", description="原文依据句")

    def key(self) -> str:
        return f"{self.analyst}|{self.symbol}|{self.published_on.isoformat()}|{self.direction}"


class Outcome(BaseModel):
    """一张观点卡的回溯结果。"""

    card: OpinionCard
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    start_price: Optional[float] = None
    end_price: Optional[float] = None
    pct: Optional[float] = None          # 期间涨跌幅（小数）
    hit: Optional[bool] = None           # 对/错；None = 未到期或无行情
    pnl: Optional[float] = None          # 按方向折算的收益（小数）
    note: str = ""

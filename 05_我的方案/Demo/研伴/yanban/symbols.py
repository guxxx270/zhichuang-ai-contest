"""品种词典：中文名 → 代码；代码 → 板块。晨读、问典、代笔都用它对齐。"""
from __future__ import annotations

SYMBOLS: dict[str, str] = {
    "螺纹钢": "RB", "螺纹": "RB", "热卷": "HC", "热轧卷板": "HC", "铁矿石": "I", "铁矿": "I",
    "焦炭": "J", "焦煤": "JM", "动力煤": "ZC",
    "沪铜": "CU", "铜": "CU", "沪铝": "AL", "铝": "AL", "沪锌": "ZN", "锌": "ZN", "沪镍": "NI", "镍": "NI",
    "黄金": "AU", "沪金": "AU", "白银": "AG", "沪银": "AG",
    "原油": "SC", "燃料油": "FU", "沥青": "BU", "PTA": "TA", "甲醇": "MA", "乙二醇": "EG", "聚丙烯": "PP", "塑料": "L",
    "豆粕": "M", "豆油": "Y", "棕榈油": "P", "玉米": "C", "生猪": "LH", "白糖": "SR", "棉花": "CF",
    "沪深300": "IF", "中证500": "IC", "中证1000": "IM", "十年期国债": "T", "国债": "T",
}
SYMBOL_NAMES: dict[str, str] = {}
for _n, _c in SYMBOLS.items():
    SYMBOL_NAMES.setdefault(_c, _n)

SECTORS: dict[str, str] = {
    **{c: "黑色" for c in ("RB", "HC", "I", "J", "JM", "ZC")},
    **{c: "有色" for c in ("CU", "AL", "ZN", "NI", "AU", "AG")},
    **{c: "能化" for c in ("SC", "FU", "BU", "TA", "MA", "EG", "PP", "L")},
    **{c: "农产品" for c in ("M", "Y", "P", "C", "LH", "SR", "CF")},
    **{c: "金融" for c in ("IF", "IC", "IM", "T")},
}


def detect_symbols(text: str) -> list[str]:
    found: dict[str, int] = {}
    for name, code in SYMBOLS.items():
        pos = text.find(name)
        if pos >= 0 and (code not in found or pos < found[code]):
            found[code] = pos
    return [c for c, _ in sorted(found.items(), key=lambda kv: kv[1])]


def name_of(code: str) -> str:
    return SYMBOL_NAMES.get(code.upper(), code)

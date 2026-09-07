"""品种词典：中文名 → 代码；代码 → 板块。晨读、问典、代笔都用它对齐。"""
from __future__ import annotations

SYMBOLS: dict[str, str] = {
    "螺纹钢": "RB", "螺纹": "RB", "热卷": "HC", "热轧卷板": "HC", "铁矿石": "I", "铁矿": "I",
    "焦炭": "J", "焦煤": "JM", "动力煤": "ZC", "锰硅": "SM", "硅铁": "SF",
    "沪铜": "CU", "铜": "CU", "沪铝": "AL", "铝": "AL", "氧化铝": "AO", "沪锌": "ZN", "锌": "ZN", "沪镍": "NI", "镍": "NI",
    "碳酸锂": "LC", "工业硅": "SI", "多晶硅": "PS",
    "黄金": "AU", "沪金": "AU", "白银": "AG", "沪银": "AG",
    "原油": "SC", "燃料油": "FU", "沥青": "BU", "PTA": "TA", "PX": "PX", "甲醇": "MA", "乙二醇": "EG", "聚丙烯": "PP", "塑料": "L",
    "PVC": "V", "纯碱": "SA", "玻璃": "FG", "烧碱": "SH", "短纤": "PF", "瓶片": "PR", "橡胶": "RU",
    "豆粕": "M", "豆油": "Y", "棕榈油": "P", "菜油": "OI", "豆一": "A", "玉米": "C", "生猪": "LH", "白糖": "SR", "棉花": "CF",
    "鸡蛋": "JD", "苹果": "AP", "花生": "PK", "红枣": "CJ",
    "沪深300": "IF", "中证500": "IC", "中证1000": "IM", "十年期国债": "T", "国债": "T",
}

# 板块/组合段落标题 → 该段说的其实是哪些品种（「油脂：……」段归豆油/棕榈油，不归里面顺带提到的原油）
SECTOR_ALIASES: dict[str, list[str]] = {
    "油脂": ["Y", "P", "OI"], "油料": ["M", "Y"], "粕类": ["M"], "油脂油料": ["M", "Y", "P"],
    "钢材": ["RB", "HC"], "煤焦": ["J", "JM"], "双焦": ["J", "JM"], "贵金属": ["AU", "AG"],
    "铁合金": ["SM", "SF"], "聚酯": ["TA", "EG", "PF", "PR"],
}
# 段落标题是这些词时，段内提到谁就算谁的（综述型段落）
GENERIC_HEADS = {"观点", "核心观点", "综述", "总结", "策略", "操作策略", "宏观", "市场", "今日", "早评", "日评", "要闻", "资讯", "总体"}
SYMBOL_NAMES: dict[str, str] = {}
for _n, _c in SYMBOLS.items():
    SYMBOL_NAMES.setdefault(_c, _n)

SECTORS: dict[str, str] = {
    **{c: "黑色" for c in ("RB", "HC", "I", "J", "JM", "ZC", "SM", "SF")},
    **{c: "有色" for c in ("CU", "AL", "AO", "ZN", "NI", "AU", "AG", "LC", "SI", "PS")},
    **{c: "能化" for c in ("SC", "FU", "BU", "TA", "PX", "MA", "EG", "PP", "L", "V", "SA", "FG", "SH", "PF", "PR", "RU")},
    **{c: "农产品" for c in ("M", "Y", "P", "OI", "A", "C", "LH", "SR", "CF", "JD", "AP", "PK", "CJ")},
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

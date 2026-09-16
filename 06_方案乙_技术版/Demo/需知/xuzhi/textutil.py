"""文本小工具与领域词典（品种、部门、渠道、数据源等）。词典是示例，可按公司实际扩充。"""
from __future__ import annotations

import re

SENT_SPLIT = re.compile(r"[。；;！!？?\n]+")

# 品种词典：别名 → 标准名
SYMBOLS: dict[str, str] = {
    "螺纹": "螺纹钢", "螺纹钢": "螺纹钢", "热卷": "热轧卷板", "热轧": "热轧卷板", "铁矿": "铁矿石", "铁矿石": "铁矿石",
    "焦炭": "焦炭", "焦煤": "焦煤", "动力煤": "动力煤", "沪铜": "沪铜", "铜": "沪铜", "沪铝": "沪铝", "铝": "沪铝",
    "锌": "沪锌", "镍": "沪镍", "黄金": "黄金", "白银": "白银", "原油": "原油", "PTA": "PTA", "甲醇": "甲醇",
    "豆粕": "豆粕", "豆油": "豆油", "棕榈油": "棕榈油", "白糖": "白糖", "棉花": "棉花", "玉米": "玉米", "生猪": "生猪",
    "苹果": "苹果", "纯碱": "纯碱", "玻璃": "玻璃", "橡胶": "橡胶", "集运": "集运欧线", "股指": "股指期货", "国债": "国债期货",
}
SYMBOL_RE = re.compile("|".join(sorted(map(re.escape, SYMBOLS), key=len, reverse=True)))

DEPARTMENTS: list[tuple[str, str]] = [
    ("资管运营", "资管运营"), ("资管部", "资管部"), ("资管", "资管部"), ("研究所", "研究所"), ("研究员", "研究所"),
    ("投资经理", "资管部·投资经理"), ("投资部", "投资部"), ("风控部", "风控部"), ("风控", "风控部"), ("营业部", "营业部"),
    ("客户经理", "营业部"), ("合规部", "合规部"), ("合规", "合规部"), ("产业服务", "产业服务部"), ("期现", "投资部·期现"),
    ("运营", "资管运营"), ("财务", "财务部"), ("交易员", "资管部·交易"),
]
USER_ROLES = ["投资经理", "研究员", "运营", "交易员", "风控", "客户经理", "合规", "领导", "客户", "投资者"]

INDICATORS = ["单位净值", "累计净值", "净值", "回撤", "集中度", "杠杆", "保证金占用", "保证金", "风险度", "可用资金", "基差", "价差",
              "升贴水", "涨跌停", "涨跌幅", "限仓", "持仓量", "成交量", "仓单", "库存", "追保", "资金缺口", "手续费", "VaR",
              "对标指数", "持仓盈亏", "超额收益", "跟踪误差"]

DATA_SOURCE_RULES: list[tuple[str, str]] = [
    (r"行情|价格|涨跌|K线|走势|异动", "期货行情（行情服务 / 数据仓库日终）"),
    (r"现货|基差|升贴水", "现货价格（研究员维护表 / 第三方）"),
    (r"持仓|头寸|敞口|限仓|超限|手", "持仓（交易系统实时 / 结算后）"),
    (r"净值|估值|份额", "产品净值（估值核算系统，T+1）"),
    (r"结算|结算单|结算价", "结算数据（结算系统）"),
    (r"风险指标|回撤|集中度|杠杆|风险度|VaR", "风险指标（风控系统日终）"),
    (r"公告|交易所.*调整|保证金.*调|涨跌停.*调", "交易所公告（公告采集服务）"),
    (r"仓单|库存", "仓单库存（交易所日报）"),
    (r"客户|资金账号|投资者", "客户与账户信息（客户管理系统，需脱敏）"),
    (r"合同|投资范围|投资比例", "产品合同要素（产品管理）"),
]

CHANNEL_RULES: list[tuple[str, str]] = [
    (r"企业微信|企微|微信", "企业微信"), (r"邮件|邮箱", "邮件"), (r"短信", "短信"), (r"手机(?!_\d)|移动|APP|H5|随时", "手机端"),
    (r"页面|看板|网页|系统里|查询", "网页"), (r"Excel|表格", "Excel"), (r"PDF", "PDF"), (r"OA|审批", "OA"),
]

FEATURE_CUES = re.compile(r"能不能|可不可以|要|需要|希望|最好|想做|加一|加上|放进|自动|生成|计算|测算|推送|提醒|提示|通知|抓取|采集|识别|"
                          r"支持|导出|查询|展示|监控|统计|分析|留痕|发给|做一个|做个|改版|新增|增加|算出")
FILLER = re.compile(r"^(那个|对了|另外|然后|还有|其实|就是|小李|小王|在吗|跟你说个事|谢谢|请|麻烦|你好)[，,：:\s]*")


def sentences(text: str) -> list[str]:
    out = []
    for s in SENT_SPLIT.split(text):
        s = s.strip(" \t，,、")
        if len(s) >= 4:
            out.append(s)
    return out


def strip_meta(text: str) -> str:
    """去掉样例头部的『来源 / 时间 / 记录』行，只留正文。"""
    lines = [ln for ln in text.splitlines() if not re.match(r"^\s*(来源|时间|记录|主题|标题)[:：]", ln)]
    return "\n".join(lines).strip()


def detect_source(text: str, hint: str = "") -> str:
    m = re.search(r"来源[:：]\s*([^\s·]+)", text)
    if m:
        return m.group(1)
    if hint:
        return hint
    for k, v in (("微信", "企业微信"), ("邮件", "邮件"), ("纪要", "会议纪要"), ("需求说明", "需求单"), ("口述", "口述")):
        if k in text:
            return v
    return "粘贴文本"


def find_symbols(text: str) -> list[str]:
    seen: list[str] = []
    for m in SYMBOL_RE.finditer(text):
        name = SYMBOLS[m.group(0)]
        if name not in seen:
            seen.append(name)
    return seen


def label_key(s: str) -> str:
    """比较用：拉丁字母忽略大小写，中文原样。"""
    return (s or "").casefold().strip()


def same_label(a: str, b: str) -> bool:
    return bool(a and b) and label_key(a) == label_key(b)


def has_label(items: list[str], col: str) -> bool:
    return any(same_label(x, col) for x in (items or []))


def subsumed_by_longer(col: str, items: list[str]) -> bool:
    """已被更长同名覆盖（单位净值 vs 净值；大小写不敏感）。"""
    k = label_key(col)
    if not k:
        return True
    return any(k != label_key(x) and k in label_key(x) for x in (items or []) if x)


def merge_label(items: list[str], col: str) -> bool:
    """写入列表；同词不同大小写视为已有，返回是否新加。"""
    col = (col or "").strip()
    if not col or has_label(items, col) or subsumed_by_longer(col, items):
        return False
    items.append(col)
    return True


def find_indicators(text: str) -> list[str]:
    found = [i for i in INDICATORS if i in text]
    # 去掉被更长词覆盖的（单位净值 vs 净值）
    return [i for i in found if not any(i != j and i in j for j in found)]


_COL_PATTERNS = [
    re.compile(r'(?:加一列|加一栏|增加一列|新增一列|添一列)[「"“]([^」"”]{1,24})[」"”]'),
    re.compile(r'(?:加一列|加一栏|增加一列|新增一列|添一列)([^，。；;、\n]{1,24})'),
    re.compile(r'(?:在|给|向)(?:已有|原始|原有)?(?:的)?(?:表|表格|html表|列表)[^，。；;\n]{0,12}(?:里|中|内)?(?:再)?(?:新增|增加|添加|加上|加)(?:一列|一栏)?[「"“]?([^」"”，。；;、\n]{1,24})'),
    re.compile(r'新增([^，。；;、\n]{1,16})列'),
    re.compile(r'增加([^，。；;、\n]{1,16})列'),
    re.compile(r'加一列叫[「"“]?([^」"”，。；;、\n]{1,24})'),
]
_DEL_COL_PATTERNS = [
    re.compile(r'(?:删除|去掉|移除|拿掉|隐藏)(?:掉)?(?:一列|一栏|列)?[「"“]?([^」"”，。；;、\n]{1,24})'),
    re.compile(r'(?:把|将)[「"“]?([^」"”]{1,24})[」"”]?(?:这一列|这一栏|列|栏)(?:给)?(?:删除|去掉|移除|拿掉|隐藏)'),
    re.compile(r'(?:不再显示|不要|别显示)[「"“]?([^」"”]{1,16})[」"”]?(?:这一列|列)?'),
    re.compile(r'(?:表|表格)[^，。；;\n]{0,8}(?:里|中)?(?:删除|去掉|移除)[「"“]?([^」"”，。；;、\n]{1,24})'),
]
_RENAME_COL_PATTERNS = [
    re.compile(
        r'(?:把|将)[「"“]?([^」"”]{1,24})[」"”]?(?:这一列|列|栏)?'
        r'(?:改成|改为|改名[为成]?|重命名为|更名为)[「"“]?([^」"”，。；;、\n]{1,24})'
    ),
    re.compile(
        r'[「"“]?([^」"”]{1,24})[」"”]?(?:列|栏)?'
        r'(?:改成|改为|改名[为成]?|重命名为)[「"“]?([^」"”，。；;、\n]{1,24})[」"”]?(?:列|栏)?'
    ),
]
_NOT_COLUMNS = {
    "风险指标", "客户版", "一", "这个", "那个", "已有", "原始", "表里", "表格", "html",
    "一列", "一栏", "表", "列表",
}


def _clean_col_name(raw: str) -> str:
    s = re.sub(r"^(把|把这|这一|这个|叫|名为|名字叫|的)", "", (raw or "").strip("「」\"“”' 　"))
    # 拼成 blob 后正则可能吞进后续词；列名取第一个空白分段
    s = re.split(r"[\s　]+", s, maxsplit=1)[0]
    s = re.split(r"(就|放在|也要|放进去|就行|就好|旁边|里面|之中)", s, maxsplit=1)[0]
    s = re.sub(r"^(一列|一栏)", "", s)
    s = re.sub(r"(这一列|这一栏|列|栏)$", "", s)
    s = s.strip("的 「」\"“”' 　")
    if s in ("金额列", "状态列") or (s.endswith("列") and len(s) <= 3):
        s = s[:-1]
    if len(s) < 2 or len(s) > 20 or s in _NOT_COLUMNS:
        return ""
    return s


def find_added_columns(text: str) -> list[str]:
    """从「加一列 / 新增××列 / 在表里新增一列」里抽出列名。"""
    found: list[str] = []
    for pat in _COL_PATTERNS:
        for m in pat.finditer(text or ""):
            s = _clean_col_name(m.group(1))
            if s:
                merge_label(found, s)
    return found


def find_removed_columns(text: str) -> list[str]:
    """从「删除××列 / 去掉××」抽出要删的列名。"""
    found: list[str] = []
    for pat in _DEL_COL_PATTERNS:
        for m in pat.finditer(text or ""):
            s = _clean_col_name(m.group(1))
            if s:
                merge_label(found, s)
    return found


def find_renamed_columns(text: str) -> list[tuple[str, str]]:
    """从「把A列改成B」抽出 (旧名, 新名)。"""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pat in _RENAME_COL_PATTERNS:
        for m in pat.finditer(text or ""):
            a, b = _clean_col_name(m.group(1)), _clean_col_name(m.group(2))
            if not a or not b or same_label(a, b):
                continue
            key = (label_key(a), label_key(b))
            if key in seen:
                continue
            seen.add(key)
            found.append((a, b))
    return found


def match_rules(text: str, rules: list[tuple[str, str]]) -> list[str]:
    out: list[str] = []
    for pat, label in rules:
        if re.search(pat, text) and label not in out:
            out.append(label)
    return out


BACKGROUND = re.compile(r"^(目前|现在|当前|现状|背景|每逢|近年|由于|因为)|耗时长|易出错|领导(表示|同意)|周会要看|请技术部评估|说明这个需求")


def clean_feature(s: str) -> str:
    s = FILLER.sub("", s.strip())
    s = re.sub(r"^[^，,。]{2,14}(提出|表示|补充|反馈|建议|说)[，,：:]?", "", s)
    s = re.sub(r"^\d+[\.、]\s*", "", s)
    s = re.sub(r"^(能不能|可不可以|希望|最好|想|想做|需要|要)(做|把|在|加|有|能)?", "", s)
    return s.strip("，, ")

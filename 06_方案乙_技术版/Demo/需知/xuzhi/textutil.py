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
              "升贴水", "涨跌停", "涨跌幅", "限仓", "持仓量", "成交量", "仓单", "库存", "追保", "资金缺口", "手续费", "VaR"]

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
    for k, v in (("微信", "企业微信"), ("邮件", "邮件"), ("纪要", "会议纪要"), ("需求说明", "需求单")):
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


def find_indicators(text: str) -> list[str]:
    found = [i for i in INDICATORS if i in text]
    # 去掉被更长词覆盖的（单位净值 vs 净值）
    return [i for i in found if not any(i != j and i in j for j in found)]


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

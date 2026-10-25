"""隐盾：文本进模型前把敏感实体换成语义标签，用完再还原（纯规则、不联网）。

识别规则与团队作品「需知」v0.6 的 xuzhi/privacy.py 同源；独立版另加：带单位的金额、技术凭据（私钥、
连接串口令、sk- / ghp_ / AKIA 等令牌）、按类型开关、不脱敏白名单、多轮会话、容错还原（模型把标签换了
写法也认得）与疑似遗漏提示。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable

# 页面与文档里的展示顺序
KINDS = ["客户", "机构", "手机", "电话", "身份证", "账号", "邮箱", "内网地址", "密钥", "金额"]

# 凭据先认：私钥块、连接串里的口令里有数字和 @，先换掉免得被拆成账号、邮箱
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----")
_URL_CRED = re.compile(r"(?<=://)[^\s:/@<>]+:[^\s/@<>]+(?=@)")
_TOKENS = re.compile(
    r"\bsk-[A-Za-z0-9_\-]{16,}"                        # OpenAI 兼容网关的 Key
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"
    r"|\bAKIA[0-9A-Z]{16}\b|\bLTAI[A-Za-z0-9]{12,30}\b"  # AWS / 阿里云 AccessKey
    r"|\bxox[abprs]-[A-Za-z0-9\-]{10,}"
    r"|\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"   # JWT
)
# 「api_key=…」「"password": "…"」「密码：…」只换值，留下键名，模型仍知道这是哪一项配置；
# 值只认 ASCII，免得把「密码：请联系管理员」整句吞掉
_SECRET_VAL = r"([^\s'\"`,;<>()\[\]{}\u0080-\uffff]{6,})"
_SECRET_KV = re.compile(
    r"((?i:api[_-]?key|access[_-]?key|secret[_-]?key|client[_-]?secret|secret|token|password|passwd|pwd)|密码|口令|密钥)"
    r"([\"']?\s*[:：=]\s*[\"']?)" + _SECRET_VAL)
_BEARER = re.compile(r"((?i:bearer))(\s+)" + _SECRET_VAL)
# 中文键名后常直接跟空格：「root 口令 J8m!q2Xz9w」「密码 Abc12345」；值仍只认 ASCII，「密码长度至少 8 位」不动
_SECRET_CN = re.compile(r"(密码|口令|密钥)((?:是|为)?\s*[:：=]?\s*)" + _SECRET_VAL)

# 先认分段写法（身份证 6-8-4、银行卡 4-4-4-4），再认手机；否则分组号码会被拆成几段漏掉
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("身份证", re.compile(r"(?<!\d)\d{6}[ -]?(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])[ -]?\d{3}[\dXx](?!\d)")),
    # 分组银行卡 4-4-4-4，可再接 1～3 位；隔空格时只认完整的 3 位一组，免得吞掉后面的「12 月」
    ("账号", re.compile(r"(?<!\d)\d{4}(?:[ -]\d{4}){3}(?:[ -]\d{3}|\d{1,3})?(?!\d)")),
    ("手机", re.compile(r"(?<![\d+])(?:\+86[ -]?)?1[3-9]\d(?:[ -]?\d{4}){2}(?!\d)")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("账号", re.compile(r"(?<![\d.])\d{10,19}(?![\d.])")),
    ("内网地址", re.compile(r"(?<!\d)(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}(?!\d)")),
    ("电话", re.compile(r"(?<![\d-])0\d{2,3}-\d{7,8}(?:-\d{1,4})?(?![\d-])")),
]
# 金额：千分位大数、带「元」的金额、¥ 开头；「万 / 亿」不带「元」时要前面有资金语境（权益 860 万），
# 「成交 300 万手」「1.2 万亿的市场」不算。单价（3650 元/吨）是行情，不换
_MONEY_TAIL = r"(?!\s*(?:万元|亿元|元)?\s*[/每])"
_MONEY = re.compile(
    r"(?<![\d.,])(?:[¥￥]\s?\d[\d,]*(?:\.\d+)?(?:\s*(?:万|亿)元?)?"
    r"|\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?(?:\s*(?:万元|亿元|元))?"
    r"|\d{1,3}(?:,\d{3})+(?:\.\d+)?\s*(?:万元|亿元|元)"
    r"|\d+(?:\.\d+)?\s*(?:万元|亿元)"
    r"|\d{4,}(?:\.\d+)?\s*元)" + _MONEY_TAIL)
_MONEY_WAN = re.compile(r"(?<![\d.,])\d+(?:\.\d+)?\s*(?:万|亿)(?![亿元手吨张股人次个份条笔户家辆件字行年天斤克倍多])" + _MONEY_TAIL)
_MONEY_CTX = re.compile(r"(?:盈|亏|权益|资金|保证金|入金|出金|余额|资产|市值|金额|收益|利润|损失|欠款|追保|缺口|本金|赔|转账|划转|"
                        r"授信|额度|成本|贷款|存款|理财|规模|申购|赎回|净值|收入|费用|报价|合同|成交额|货款|转出|转入|到账|汇款|打款|还款|借款|垫付)")

# 6～9 位的短账号单看是普通数字（300 手、日期），只在「资金账号：」「账号是」这类上下文词后才算，且只换数字
_SHORT_ACCOUNT = re.compile(r"((?:资金账号|资金账户|客户编号|客户号|交易编码|账号|账户|卡号)(?:是|为)?[:：]?\s*)(\d{6,9})(?!\d|\.\d)")
_ORG_SUFFIX = "公司|集团|基金|资管|投资|私募|厂|证券|期货|银行|信托|保险|资本"
# 只看机构名本身含不含这些词（「客户信息需符合公司…」）；名字后面跟「要求 / 需要」照样认（「客户中信期货要求…」）
_ORG_CTX = re.compile(r"((?:客户|机构|投资者)(?:是|为)?[:：]?\s*)((?:(?!需|符合|通知|要求|信息|经理)[一-龥]){2,6}(?:" + _ORG_SUFFIX + "))")
# 「客户为期货公司」「客户交易期货」「客户想做期货」「客户大多是银行」只是泛称、动作或意愿，不是机构名
_ORG_GENERIC = {"机构", "这家", "那家", "一家", "各家", "某家", "其他", "相关", "大型", "中小", "外资", "国有", "商业", "合作"}
_ORG_VERB = re.compile(r"^(?:交易|买卖|参与|开户|持有|购买|所在|使用|选择|关注|对接|咨询|投资|做|在|去|到|从|向|对|和|与|及"
                       r"|想|要|希望|准备|打算|计划|喜欢|偏好|关心|比较|了解|懂|开通|申请|办理|买|卖|炒|接触|配置|属于|包括|来自|涉及"
                       r"|都是|都在|都要|都想|都有|都做|也|还|没|不|已|可以|能(?!源|投)|会|主要|大多|大部分|部分|多是|多为|多数|少数"
                       r"|基本|一般|只|全是|全部|全都|很多|一些|有些|里面|其中)")
# 常见姓氏：不在表里的字不当姓（无上下文的裸姓名请用自定义名单）
SURNAMES = "王李张刘陈杨赵黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤尹黎易常武乔贺赖龚文"
_GIVEN = r"(?:(?![的要也在说问想已是和与对都就被把给])[一-龥])"   # 名字里不含这些虚词，免得把「客户李也」「客户方是谁」认成姓名
_NAME_CTX = re.compile(r"((?:客户|投资者)[:：]?\s*)([" + SURNAMES + r"]" + _GIVEN + r"{1,2}(?:先生|女士|总)?)"
                       r"(?=的|先生|女士|要|也|在|说|问|想|已|反|来|称|表示|提|咨询|投诉|致电|打|回|申请|希望|认为|觉得|对|和|与|跟|还|又|再|今天|昨天"
                       r"|明天|刚|一直|目前|名下|账户|账号|手机|电话|持有|买|卖|做|是|都|就|被|让|把|给|向|从|[^一-龥]|$)")
_HONORIFIC = re.compile(r"([" + SURNAMES + r"][一-龥]{0,2})(先生|女士)")
# 紧挨在已换掉的手机 / 账号 / 身份证 / 邮箱 / 座机前面的 2～3 字姓名（「张伟（<手机_1>」「陈立（<邮箱_1>」）也是客户
_NAME_BEFORE_ID = re.compile(r"(?:(?<![一-龥])|(?<=[位是叫找给向和与及人系请由让]))([" + SURNAMES + r"]" + _GIVEN + r"{1,2})"
                             r"(?=\s*[（(：:]?\s*<(?:手机|账号|身份证|邮箱|电话)_\d+>)")
# 以姓氏字开头的常用词与期货行情词：不当姓名（客户黄金持仓、客户周报、联系方式）
_NAME_STOP = {
    "周报", "周末", "周期", "周内", "周度", "周一", "周二", "周三", "周四", "周五", "周六", "周日", "周天", "方案", "方向", "方面",
    "方式", "方法", "高级", "高端", "金额", "余额", "名单", "范围", "顾问", "马上", "曾经", "任何", "任务", "何时", "许多", "陆续",
    "文件", "文档", "程序", "常用", "常见", "易用", "毛利", "钱款", "白名单", "黑名单", "万元", "史上",
    "黄金", "白银", "白糖", "原油", "焦炭", "焦煤", "甲醇", "玉米", "苹果", "纯碱", "玻璃", "棉花", "生猪", "豆粕", "豆油", "铁矿",
    "螺纹", "热卷", "热轧", "沪铜", "沪铝", "国债", "股指", "集运", "焦煤", "动力煤", "棕榈油", "橡胶", "期货", "合约", "持仓",
    "保证金", "净值", "回撤", "杠杆", "基差", "价差", "升贴水", "涨跌", "限仓", "成交", "仓单", "库存", "追保", "手续费", "资金",
    "集中度", "风险度", "超额收益", "跟踪误差", "对标指数",
    # 姓氏字开头、后面常跟「来 / 打 / 说」的日常词：「客户白天来电」「客户许久没来」
    "白天", "黄昏", "许久", "常常", "常来", "余下", "方反", "石化", "康复", "江湖", "段位", "雷同", "钟表", "易变", "易出", "孔径",
    "万一", "龙头", "程序化", "金融", "高度", "高频", "高净值", "夏季", "江浙", "苏州", "田间", "文化", "史无", "钱款",
}
# 同事称谓（王总、张老师、李经理）不当客户；「客户王总」前面点明了是客户，照常换
_COLLEAGUE = re.compile(r"(?:总|老师|经理|主任|主管|工|部长|总监|处长|科长|老板|同事)$")
_LABEL_RE = re.compile(r"\\?<([^\s<>\\_]{1,8})\\?_(\d+)\\?>")

# 贴给大模型时放在最前面的一句提示：模型照着保留标签，回来才还原得了
PROMPT_HINT = "（以下内容已脱敏：形如 <客户_1> 的标记是占位符，回答时请原样保留，不要改写、合并或猜测其真实内容。）"


_DIGIT_KINDS = {"手机", "电话", "账号", "身份证"}


def _norm(kind: str, original: str) -> str:
    """同一个号码不同写法（139 1234 5678、+86 13912345678、13912345678）算同一实体，用同一标签。"""
    if kind not in _DIGIT_KINDS:
        return original
    s = re.sub(r"[\s\-]", "", original)
    return s[3:] if kind == "手机" and s.startswith("+86") else s.upper()


def _name_ok(name: str) -> bool:
    return not any(w in name for w in _NAME_STOP)


def _name_before_id_ok(name: str) -> bool:
    return _name_ok(name) and not _COLLEAGUE.search(name)


def _org_ok(name: str) -> bool:
    if "的" in name:   # 只排除紧邻搭配：「客户的期货公司」
        return False
    core = name
    while True:
        m = re.search(r"(?:" + _ORG_SUFFIX + r")$", core)
        if not m or m.start() == 0:
            break
        core = core[: m.start()]
    if re.fullmatch(r"(?:" + _ORG_SUFFIX + r")+", core):
        return False
    return core not in _ORG_GENERIC and not _ORG_VERB.match(core)


def label_counts(src: str | dict | None) -> dict[str, int]:
    """已有隐盾标签各类别的最大编号（含 Markdown 转义成 \\<手机\\_1\\> 的写法）；新标签接着编，免得撞号还原错位。"""
    if isinstance(src, dict):
        text = " ".join(f"{k} {v}" for k, v in src.items())
    else:
        text = src or ""
    counts: dict[str, int] = {}
    for m in _LABEL_RE.finditer(text):
        kind, n = m.group(1), int(m.group(2))
        counts[kind] = max(counts.get(kind, 0), n)
    return counts


def labels_in(text: str) -> list[str]:
    """文本里已有的隐盾标签（统一成未转义写法），按出现顺序去重。"""
    out: list[str] = []
    for m in _LABEL_RE.finditer(text or ""):
        label = f"<{m.group(1)}_{m.group(2)}>"
        if label not in out:
            out.append(label)
    return out


@dataclass
class RedactResult:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


@dataclass
class RestoreResult:
    text: str
    restored: int = 0                                  # 换回原文的处数
    fixed: int = 0                                     # 其中模型改过写法（〈客户_1〉、\<客户\_1\>、客户1…）的处数
    unknown: list[str] = field(default_factory=list)   # 回答里像标签、映射表里却没有的（多半是模型自己编的）


class Redactor:
    def __init__(self, names: Iterable[str] | None = None, orgs: Iterable[str] | None = None,
                 keep: Iterable[str] | None = None, kinds: Iterable[str] | None = None,
                 terms: dict[str, Iterable[str]] | None = None) -> None:
        """names / orgs：自定义客户与机构名单；terms：其他类型的自定义词（{"账号": ["12345678"]}）——名单不论类型开关，
        一律换。keep：不脱敏白名单（公司热线、自家公司名）。kinds：要换的类型，默认全部（见 KINDS）。"""
        words: dict[str, str] = {}
        for kind, items in [("客户", names), ("机构", orgs), *((terms or {}).items())]:
            for w in items or []:
                if w and w.strip():
                    words.setdefault(w.strip(), kind)
        self.words = sorted(words.items(), key=lambda kv: len(kv[0]), reverse=True)   # 长词先换：「华东某某投资公司」先于「华东」
        self.keep = {k.strip() for k in keep or [] if k and k.strip()}
        self.kinds = set(KINDS if kinds is None else kinds)

    def redact(self, text: str, start: dict[str, int] | None = None, known: dict[str, str] | None = None) -> RedactResult:
        """start：各类别已用到的编号（新标签接着编）；known：原文 → 已有标签（同一原文沿用同一标签）。
        counts 只统计本次新换掉的条数。"""
        mapping: dict[str, str] = {}
        reverse: dict[tuple[str, str], str] = {}
        counts: dict[str, int] = {}
        numbers = dict(start or {})
        for original, label in (known or {}).items():
            m = _LABEL_RE.fullmatch(label or "")
            if m and original and original != label:
                reverse[(m.group(1), _norm(m.group(1), original))] = label

        def tag(kind: str, original: str) -> str:
            if original in self.keep or re.sub(r"(?:先生|女士|总)$", "", original) in self.keep:
                return original
            k = (kind, _norm(kind, original))
            if k not in reverse:
                numbers[kind] = numbers.get(kind, 0) + 1
                counts[kind] = counts.get(kind, 0) + 1
                reverse[k] = f"<{kind}_{numbers[kind]}>"
            mapping.setdefault(reverse[k], original)   # 同一号码换了写法，还原成第一次出现的写法
            return reverse[k]

        def keep_or_tag(kind: str, m: re.Match, ok) -> str:
            return m.group(1) + tag(kind, m.group(2)) if ok(m.group(2)) else m.group(0)

        def money_wan(m: re.Match) -> str:
            before = m.string[max(0, m.start() - 8): m.start()]
            return tag("金额", m.group(0)) if _MONEY_CTX.search(before) else m.group(0)

        on = self.kinds.__contains__
        out = text
        for word, kind in self.words:    # 名单先换：用户点名的词整词换掉，不让后面的规则只切走一半
            if word in out:
                out = out.replace(word, tag(kind, word))
        if on("密钥"):
            out = _PEM.sub(lambda m: tag("密钥", m.group(0)), out)
            out = _URL_CRED.sub(lambda m: tag("密钥", m.group(0)), out)
            out = _TOKENS.sub(lambda m: tag("密钥", m.group(0)), out)
            for pat in (_SECRET_KV, _BEARER, _SECRET_CN):
                out = pat.sub(lambda m: m.group(1) + m.group(2) + tag("密钥", m.group(3)), out)
        for kind, pat in _PATTERNS:
            if on(kind):
                out = pat.sub(lambda m, kind=kind: tag(kind, m.group(0)), out)
        if on("账号"):
            out = _SHORT_ACCOUNT.sub(lambda m: m.group(1) + tag("账号", m.group(2)), out)
        if on("金额"):
            out = _MONEY.sub(lambda m: tag("金额", m.group(0)), out)
            out = _MONEY_WAN.sub(money_wan, out)
        if on("机构"):
            out = _ORG_CTX.sub(lambda m: keep_or_tag("机构", m, _org_ok), out)
        if on("客户"):
            out = _HONORIFIC.sub(lambda m: (tag("客户", m.group(1)) if _name_ok(m.group(1)) else m.group(1)) + m.group(2), out)
            out = _NAME_CTX.sub(lambda m: keep_or_tag("客户", m, _name_ok), out)
            out = _NAME_BEFORE_ID.sub(lambda m: tag("客户", m.group(1)) if _name_before_id_ok(m.group(1)) else m.group(0), out)
        out, mapping = _renumber(out, mapping, start or {})
        return RedactResult(text=out, mapping=mapping, counts=counts)

    @staticmethod
    def restore(text: str, mapping: dict[str, str]) -> str:
        return restore_detail(text, mapping).text


def _renumber(text: str, mapping: dict[str, str], start: dict[str, int]) -> tuple[str, dict[str, str]]:
    """规则分几遍跑，新标签的编号是按规则顺序给的；这里按在文中第一次出现的先后重排（<金额_1> 在 <金额_2> 前面）。
    沿用的旧标签（编号不超过 start）不动。"""
    order: dict[str, list[str]] = {}
    for label in labels_in(text):
        if label in mapping:
            kind, n = _LABEL_RE.fullmatch(label).groups()
            if int(n) > start.get(kind, 0):
                order.setdefault(kind, []).append(label)
    rename = {old: f"<{kind}_{start.get(kind, 0) + i}>" for kind, olds in order.items() for i, old in enumerate(olds, 1)}
    if all(old == new for old, new in rename.items()):
        return text, mapping
    text = _LABEL_RE.sub(lambda m: rename.get(m.group(0), m.group(0)), text)
    return text, {rename.get(label, label): original for label, original in mapping.items()}


# ---------- 容错还原 ----------
# 模型常把 <客户_1> 改写成 〈客户_1〉、【客户_1】、\<客户\_1\>（Markdown 转义）、&lt;客户_1&gt;、<客户1>、客户_1……
_PAIRS = [("<", ">"), ("＜", "＞"), ("〈", "〉"), ("《", "》"), ("【", "】"), ("[", "]"), ("「", "」"), ("{", "}"), ("〔", "〕"),
          ("&lt;", "&gt;")]
_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def _restore_re(kinds: Iterable[str]) -> re.Pattern:
    kind = "(?:" + "|".join(sorted(map(re.escape, set(kinds)), key=len, reverse=True)) + ")"
    num = r"[0-9０-９]{1,4}"
    body = kind + r"\s*\\?[_＿\-－·]?\s*" + num
    paired = [r"\\?" + re.escape(o) + r"\s*" + body + r"\s*\\?" + re.escape(c) for o, c in _PAIRS]
    bare = r"(?<![A-Za-z0-9])" + kind + r"\\?[_＿]" + num + r"(?![0-9０-９])"
    return re.compile("|".join(paired + [bare]))


def restore_detail(text: str, mapping: dict[str, str], wrap: Callable[[str, str], str] | None = None) -> RestoreResult:
    """按映射表把标签换回原文，一遍替换（不会把换回来的原文再换一次）。wrap(原文, 标签) 可定制换回去的样子
    （页面上用它给还原处上色）。"""
    text = text or ""
    known: dict[str, str] = {}
    for label, original in (mapping or {}).items():
        m = _LABEL_RE.fullmatch(label)
        if m:
            known[f"<{m.group(1)}_{int(m.group(2))}>"] = original
    res = RestoreResult(text=text)
    if known:
        kinds = {_LABEL_RE.fullmatch(lb).group(1) for lb in known}
        kind_re = re.compile("|".join(sorted(map(re.escape, kinds), key=len, reverse=True)))

        def sub(m: re.Match) -> str:
            raw = m.group(0)
            kind = kind_re.search(raw).group(0)
            n = int(re.search(r"[0-9０-９]+(?=\D*$)", raw).group(0).translate(_FULLWIDTH))
            label = f"<{kind}_{n}>"
            if label not in known:
                res.unknown.append(raw)
                return raw
            res.restored += 1
            if raw != label:
                res.fixed += 1
            return wrap(known[label], label) if wrap else known[label]

        text = _restore_re(kinds).sub(sub, text)
    # 映射表里没有的类型：<地址_1> 这类也提示出来
    for m in _LABEL_RE.finditer(text):
        if f"<{m.group(1)}_{int(m.group(2))}>" not in known:
            res.unknown.append(m.group(0))
    res.unknown = list(dict.fromkeys(res.unknown))
    res.text = text
    return res


# ---------- 多轮会话 ----------
class Session:
    """多轮对话：同一实体始终同一标签，新实体接着编号；映射表累计，任何一轮的回答都能还原。"""

    def __init__(self, redactor: Redactor | None = None, mapping: dict[str, str] | None = None) -> None:
        self.redactor = redactor or Redactor()
        self.mapping: dict[str, str] = dict(mapping or {})

    def redact(self, text: str) -> RedactResult:
        known = {original: label for label, original in self.mapping.items()}
        r = self.redactor.redact(text, start=label_counts(self.mapping), known=known)
        for label, original in r.mapping.items():
            self.mapping.setdefault(label, original)
        return r

    def restore(self, text: str, wrap: Callable[[str, str], str] | None = None) -> RestoreResult:
        return restore_detail(text, self.mapping, wrap)


# ---------- 疑似遗漏 ----------
_BARE_NUM = re.compile(r"(?<![\d.:/\-_A-Za-z])\d{6,9}(?![\d.:/\-A-Za-z年月日号点时分秒%％手吨张股元])")
_DATE8 = re.compile(r"(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])")


def suspects(redacted: str) -> list[str]:
    """脱敏后的文本里还剩的、规则不敢定的可疑项：没有上下文的 6～9 位数字（可能是资金账号、客户号）。
    只提示，不自动换；用户确认后加进名单再脱敏一次。"""
    out: list[str] = []
    for m in _BARE_NUM.finditer(redacted or ""):
        s = m.group(0)
        if _DATE8.fullmatch(s) or s in out:
            continue
        out.append(s)
    return out

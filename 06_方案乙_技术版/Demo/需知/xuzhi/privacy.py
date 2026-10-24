"""隐盾：文本进模型前把敏感实体换成语义标签，用完再还原（沿用研伴，纯规则、不联网）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import textutil

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
    ("密钥", re.compile(r"(?i)(?:api[_-]?key|token|secret|password|密码)\s*[:：=]\s*\S{6,}")),
    ("金额", re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?(?:元|万元|亿元)?")),
]
# 6～9 位的短账号单看是普通数字（300 手、日期），只在「资金账号：」这类上下文词后才算，且只换数字
_SHORT_ACCOUNT = re.compile(r"((?:资金账号|资金账户|客户编号|客户号|交易编码|账号|账户|卡号)[:：]?\s*)(\d{6,9})(?!\d|\.\d)")
_ORG_SUFFIX = "公司|集团|基金|资管|投资|私募|厂|证券|期货|银行|信托|保险|资本"
_ORG_CTX = re.compile(r"((?:客户|机构|投资者)(?:是|为)?[:：]?\s*)((?![^，。,]*(?:需|符合|通知|要求|信息|经理))[一-龥]{2,6}(?:" + _ORG_SUFFIX + "))")
# 「客户为期货公司」「客户交易期货」只是泛称或动作，不是机构名
_ORG_GENERIC = {"机构", "这家", "那家", "一家", "各家", "某家", "其他", "相关", "大型", "中小", "外资", "国有", "商业", "合作"}
_ORG_VERB = re.compile(r"^(?:交易|买卖|参与|开户|持有|购买|所在|使用|选择|关注|对接|咨询|投资|做|在|去|到|从|向|对|和|与|及)")
_SURNAMES = textutil.SURNAMES   # 与问清剥称呼共用一份姓氏表
_GIVEN = r"(?:(?![的要也在说问想已])[一-龥])"   # 名字里不含这些虚词，免得把「客户李也」认成姓名
_NAME_CTX = re.compile(r"((?:客户|投资者)[:：]?\s*)([" + _SURNAMES + r"]" + _GIVEN + r"{1,2}(?:先生|女士|总)?)"
                       r"(?=的|先生|女士|要|也|在|说|问|想|已|[^一-龥]|$)")
_HONORIFIC = re.compile(r"([" + _SURNAMES + r"][一-龥]{0,2})(先生|女士)")
# 紧挨在已换掉的手机 / 账号 / 身份证前面的 2～3 字姓名（「张伟（<手机_1>」）也是客户
_NAME_BEFORE_ID = re.compile(r"(?:(?<![一-龥])|(?<=[位是叫找给向和与及人]))([" + _SURNAMES + r"]" + _GIVEN + r"{1,2})"
                             r"(?=\s*[（(：:]?\s*<(?:手机|账号|身份证)_\d+>)")
# 以姓氏字开头的常用词：不当姓名（客户黄金持仓、客户周报、联系方式）
_NAME_STOP = {
    "周报", "周末", "周期", "周内", "周度", "周一", "周二", "周三", "周四", "周五", "周六", "周日", "周天", "方案", "方向", "方面",
    "方式", "方法", "高级", "高端", "金额", "余额", "名单", "范围", "顾问", "马上", "曾经", "任何", "任务", "何时", "许多", "陆续",
    "文件", "文档", "程序", "常用", "常见", "易用", "毛利", "钱款", "白名单", "黑名单", "万元", "史上",
}
_STOPS = tuple(w for w in _NAME_STOP | set(textutil.SYMBOLS) | set(textutil.INDICATORS) if len(w) >= 2)
# 同事称谓（王总、张老师、李经理）不当客户；「客户王总」前面点明了是客户，照常换
_COLLEAGUE = re.compile(r"(?:总|老师|经理|主任|主管|工|部长|总监|处长|科长|老板|同事)$")
_LABEL_RE = re.compile(r"\\?<([^\s<>\\_]{1,8})\\?_(\d+)\\?>")


def _name_ok(name: str) -> bool:
    return not any(w in name for w in _STOPS)


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


class Redactor:
    def __init__(self, names: list[str] | None = None, orgs: list[str] | None = None) -> None:
        self.names = sorted(set(names or []), key=len, reverse=True)
        self.orgs = sorted(set(orgs or []), key=len, reverse=True)

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
                reverse[(m.group(1), original)] = label

        def tag(kind: str, original: str) -> str:
            k = (kind, original)
            if k not in reverse:
                numbers[kind] = numbers.get(kind, 0) + 1
                counts[kind] = counts.get(kind, 0) + 1
                reverse[k] = f"<{kind}_{numbers[kind]}>"
            mapping[reverse[k]] = original
            return reverse[k]

        def keep_or_tag(kind: str, m: re.Match, ok) -> str:
            return m.group(1) + tag(kind, m.group(2)) if ok(m.group(2)) else m.group(0)

        out = text
        for kind, pat in _PATTERNS:
            out = pat.sub(lambda m, kind=kind: tag(kind, m.group(0)), out)
        out = _SHORT_ACCOUNT.sub(lambda m: m.group(1) + tag("账号", m.group(2)), out)
        for org in self.orgs:
            if org:
                out = out.replace(org, tag("机构", org))
        for name in self.names:
            if name:
                out = out.replace(name, tag("客户", name))
        out = _ORG_CTX.sub(lambda m: keep_or_tag("机构", m, _org_ok), out)
        out = _HONORIFIC.sub(lambda m: tag("客户", m.group(1)) + m.group(2), out)
        out = _NAME_CTX.sub(lambda m: keep_or_tag("客户", m, _name_ok), out)
        out = _NAME_BEFORE_ID.sub(lambda m: tag("客户", m.group(1)) if _name_before_id_ok(m.group(1)) else m.group(0), out)
        return RedactResult(text=out, mapping=mapping, counts=counts)

    @staticmethod
    def restore(text: str, mapping: dict[str, str]) -> str:
        for label, original in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
            text = text.replace(label, original)
        return text

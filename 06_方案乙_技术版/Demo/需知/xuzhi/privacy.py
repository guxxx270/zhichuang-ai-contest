"""隐盾：文本进模型前把敏感实体换成语义标签，用完再还原（沿用研伴，纯规则、不联网）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("身份证", re.compile(r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")),
    ("手机", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("账号", re.compile(r"(?<![\d.])\d{10,19}(?![\d.])")),
    ("内网地址", re.compile(r"(?<!\d)(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}(?!\d)")),
    ("密钥", re.compile(r"(?i)(?:api[_-]?key|token|secret|password|密码)\s*[:：=]\s*\S{6,}")),
    ("金额", re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?(?:元|万元|亿元)?")),
]
_HONORIFIC = re.compile(r"([一-龥]{2,4}?)(先生|女士)(?![一-龥])")
_ORG_CTX = re.compile(r"((?:客户|机构|投资者)[:：]?\s*)((?![^，。,]*(?:的|需|符合|通知|要求|信息|经理))[一-龥]{2,6}(?:公司|集团|基金|资管|投资|私募|厂))")
_SURNAMES = "王李张刘陈杨赵黄周吴徐孙胡朱高林何郭马罗梁宋郑谢韩唐冯于董萧程曹袁邓许傅沈曾彭吕苏卢蒋蔡贾丁魏薛叶阎余潘杜戴夏钟汪田任姜范方石姚谭廖邹熊金陆郝孔白崔康毛邱秦江史顾侯邵孟龙万段雷钱汤尹黎易常武乔贺赖龚文"
_NAME_CTX = re.compile(r"((?:客户|投资者)[:：]?\s*)([" + _SURNAMES + r"][一-龥]{1,2}(?:先生|女士|总)?)(?![一-龥])")


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

    def redact(self, text: str) -> RedactResult:
        mapping: dict[str, str] = {}
        reverse: dict[tuple[str, str], str] = {}
        counts: dict[str, int] = {}

        def tag(kind: str, original: str) -> str:
            k = (kind, original)
            if k not in reverse:
                counts[kind] = counts.get(kind, 0) + 1
                label = f"<{kind}_{counts[kind]}>"
                reverse[k] = label
                mapping[label] = original
            return reverse[k]

        out = text
        for kind, pat in _PATTERNS:
            out = pat.sub(lambda m, kind=kind: tag(kind, m.group(0)), out)
        for org in self.orgs:
            if org:
                out = out.replace(org, tag("机构", org))
        for name in self.names:
            if name:
                out = out.replace(name, tag("客户", name))
        out = _ORG_CTX.sub(lambda m: m.group(1) + tag("机构", m.group(2)), out)
        out = _HONORIFIC.sub(lambda m: tag("客户", m.group(1)) + m.group(2), out)
        out = _NAME_CTX.sub(lambda m: m.group(1) + tag("客户", m.group(2)), out)
        return RedactResult(text=out, mapping=mapping, counts=counts)

    @staticmethod
    def restore(text: str, mapping: dict[str, str]) -> str:
        for label, original in sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True):
            text = text.replace(label, original)
        return text

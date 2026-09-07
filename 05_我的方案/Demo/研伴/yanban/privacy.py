"""脱敏层（个人赛工具赛道的雏形）：文本进模型前把敏感实体换成语义标签，用完再还原。

设计要点（对照腾讯玄武 HaS 八项能力）：
- 语义保持：标签带类型（<客户_1>、<手机_1>），模型仍知道这是个客户、这是个手机号。
- 信息还原：同一实体在全文中映射到同一标签，多轮对话也一致；restore() 原样还原。
- 开集指定：可传入客户名单/机构名单做词典脱敏。
- 端侧执行：纯规则，不依赖网络。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("身份证", re.compile(r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")),
    ("手机", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("账号", re.compile(r"(?<![\d.])\d{10,19}(?![\d.])")),   # 资金账号 / 银行卡
    ("金额", re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3}){2,}(?:\.\d+)?(?:元|万元|亿元)?")),  # 带千分位的大额数字
]
_HONORIFIC = re.compile(r"([一-龥]{2,4}?)(先生|女士|总|经理)(?![一-龥])")                 # 王建国先生 → <客户_1>先生
_ORG_CTX = re.compile(r"((?:客户|机构|投资者)[:：]?\s*)([一-龥]{2,8}(?:公司|集团|基金|资管|投资|私募))")
_NAME_CTX = re.compile(r"((?:客户|投资者)[:：]?\s*)([一-龥]{2,4})(?![一-龥])")


@dataclass
class RedactResult:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)   # 标签 → 原文
    counts: dict[str, int] = field(default_factory=dict)


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

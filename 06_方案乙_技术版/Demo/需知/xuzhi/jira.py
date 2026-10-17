"""Jira 工单解析：粘贴的单条工单文本，或 Jira 导出的 CSV / Excel（批量）。

只取评估要用的字段：单号、摘要、类型、状态、优先级、模块、标签、描述（含验收标准 / 技术说明 / 复现与期望段）、
故事点、原始预估、剩余预估、实际耗时。时间统一折成小时：Jira 导出里是秒，粘贴文本里是 3d 4h / 16h / 2天 这类写法。
经办人、报告人等人名字段一律不读。"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

HOURS_PER_DAY = 8.0
DAYS_PER_WEEK = 5.0
DONE_STATUSES = ("完成", "已完成", "关闭", "已关闭", "已解决", "已上线", "done", "closed", "resolved")


@dataclass
class JiraIssue:
    key: str = ""
    summary: str = ""
    issue_type: str = ""
    status: str = ""
    priority: str = ""
    components: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    description: str = ""
    acceptance: list[str] = field(default_factory=list)
    tech_notes: str = ""
    repro: str = ""             # 缺陷：复现步骤
    expected: str = ""          # 缺陷：期望结果
    story_points: float | None = None
    original_h: float | None = None
    remaining_h: float | None = None
    spent_h: float | None = None

    @property
    def done(self) -> bool:
        return (self.status or "").strip().lower() in DONE_STATUSES and bool(self.spent_h)

    @property
    def spent_days(self) -> float | None:
        return round(self.spent_h / HOURS_PER_DAY, 2) if self.spent_h else None

    @property
    def text(self) -> str:
        """规则匹配用的全文：摘要 + 描述 + 验收 + 技术说明 + 复现 / 期望 + 模块。"""
        return "\n".join(x for x in [self.summary, self.description, *self.acceptance, self.tech_notes,
                                     self.repro, self.expected, " ".join(self.components)] if x)

    @property
    def title(self) -> str:
        return f"{self.key} {self.summary}".strip()


# ---------------------------------------------------------------- 时间
_DUR = re.compile(r"(\d+(?:\.\d+)?)\s*(人天|w|周|d|天|h|小时|m(?!s)|分钟?)", re.I)


def parse_duration_h(v) -> float | None:
    """「3d 4h」「16h」「2天」「1.5 人天」→ 小时；纯数字按小时。解析不了返回 None。"""
    s = str(v if v is not None else "").strip()
    if not s or s.lower() in ("nan", "none", "-", "—"):
        return None
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    total, hit = 0.0, False
    for num, unit in _DUR.findall(s):
        n, u = float(num), unit.lower()
        if u in ("w", "周"):
            total += n * DAYS_PER_WEEK * HOURS_PER_DAY
        elif u in ("d", "天", "人天"):
            total += n * HOURS_PER_DAY
        elif u in ("h", "小时"):
            total += n
        else:
            total += n / 60
        hit = True
    return round(total, 2) if hit else None


def _num(v) -> float | None:
    try:
        f = float(str(v).strip())
        return f if f == f else None   # 排除 NaN
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 文本清洗
def clean_markup(s: str) -> str:
    """Jira wiki 标记 → 纯文本：h3. 标题、{code}/{noformat}、*粗体*、[文字|链接]、{{等宽}}。"""
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"\{(?:code|noformat|quote|panel)(?::[^}]*)?\}", "", s)
    s = re.sub(r"^\s*h[1-6]\.\s*", "", s, flags=re.M)
    s = re.sub(r"\[([^\]|]+)\|[^\]]+\]", r"\1", s)
    s = re.sub(r"\{\{([^}]+)\}\}", r"\1", s)
    s = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"\1", s)
    s = s.replace("\\\\", "\n")
    return s.strip()


_BULLET = re.compile(r"^\s*(?:[#*\-•·]+|\d+\s*[.、)）])\s*")


def _items(block: str) -> list[str]:
    out = []
    for ln in (block or "").splitlines():
        t = _BULLET.sub("", ln).strip(" ；;。")
        if t:
            out.append(t)
    return out


# ---------------------------------------------------------------- 粘贴的单条工单
_INLINE = {
    "summary": r"摘要|标题|Summary",
    "issue_type": r"问题类型|类型|Issue\s*Type|Type",
    "status": r"状态|Status",
    "priority": r"优先级|Priority",
    "components": r"模块|组件|Components?|Component/s",
    "labels": r"标签|Labels?",
    "story_points": r"Story\s*Points?|故事点|估算点|SP",
    "original_h": r"原始预估|初始预估|Original\s*Estimate|预估工时|预估",
    "remaining_h": r"剩余预估|Remaining\s*Estimate",
    "spent_h": r"实际耗时|耗费时间|已用时间|已登记工时|实际工时|Time\s*Spent",
}
_SECTIONS = {
    "description": r"描述|Description|背景|需求描述",
    "acceptance": r"验收标准|验收条件|Acceptance\s*Criteria|完成标准|AC",
    "tech_notes": r"技术说明|技术方案|实现说明|技术备注",
    "repro": r"复现步骤|复现|Steps\s*to\s*Reproduce",
    "expected": r"期望结果|期望|Expected(?:\s*Result)?",
}
_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b")
# 段落标题后面要么是冒号，要么整行就是标题——「Active users…」不能被当成 AC 段
_SEC_RE = re.compile(r"^\s*(?:h[1-6]\.\s*)?\**\s*(" + "|".join(_SECTIONS.values()) + r")\s*\**\s*(?:[:：]\s*(.*))?$", re.I)


def _which(patterns: dict[str, str], label: str) -> str | None:
    for k, p in patterns.items():
        if re.fullmatch(p, label.strip(), flags=re.I):
            return k
    return None


def _inline_fields(line: str) -> dict[str, str]:
    """一行里「类型：Story　优先级：High　模块：报表平台」这类字段；以全角空格、竖线或两个以上空格分隔。"""
    found: dict[str, str] = {}
    for seg in re.split(r"[　|｜]|\s{2,}", line):
        m = re.match(r"^\s*([A-Za-z一-鿿 /]{1,24}?)\s*[:：]\s*(.+?)\s*$", seg)
        if m:
            k = _which(_INLINE, m.group(1))
            if k:
                found[k] = m.group(2)
    return found


def _apply_inline(issue: JiraIssue, k: str, v: str) -> None:
    if k in ("original_h", "remaining_h", "spent_h"):
        setattr(issue, k, parse_duration_h(v))
    elif k == "story_points":
        issue.story_points = _num(re.sub(r"[^\d.]", "", v) or "x")
    elif k in ("components", "labels"):
        setattr(issue, k, [x.strip() for x in re.split(r"[/、,，;；]", v) if x.strip()])
    else:
        setattr(issue, k, v.strip())


def parse_issue_text(text: str) -> JiraIssue:
    """从 Jira 详情页复制来的文字解析一条工单（字段缺什么就空着，评估时按缺失处理）。"""
    raw = clean_markup(text)
    issue = JiraIssue()
    m = _KEY_RE.search(raw)
    issue.key = m.group(1) if m else ""
    sections: dict[str, list[str]] = {}
    current = None
    head_lines: list[str] = []
    for ln in raw.splitlines():
        if not ln.strip():
            continue
        fields = _inline_fields(ln)
        if fields:
            for k, v in fields.items():
                _apply_inline(issue, k, v)
            current = None
            continue
        sm = _SEC_RE.match(ln)
        if sm and _which(_SECTIONS, sm.group(1)):
            current = _which(_SECTIONS, sm.group(1))
            if (sm.group(2) or "").strip():
                sections.setdefault(current, []).append(sm.group(2).strip())
            continue
        if current:
            sections.setdefault(current, []).append(ln)
        else:
            head_lines.append(ln)
    if not issue.summary:
        for ln in head_lines:
            t = re.sub(r"【?\s*JIRA\s*】?", "", _KEY_RE.sub("", ln), flags=re.I).strip(" :：-—[]【】")
            if t:
                issue.summary = t
                break
    issue.description = "\n".join(sections.get("description", []) + [x for x in head_lines[1:] if x.strip() != issue.summary]).strip()
    issue.acceptance = _items("\n".join(sections.get("acceptance", [])))
    if not issue.acceptance:   # 没有验收段：描述里 Given / When / Then 也算
        issue.acceptance = [x for x in _items(issue.description) if re.match(r"(?i)(?:given|when|then)\b|假设|当|那么", x)]
    issue.tech_notes = "\n".join(sections.get("tech_notes", [])).strip()
    issue.repro = "\n".join(sections.get("repro", [])).strip()
    issue.expected = "\n".join(sections.get("expected", [])).strip()
    return issue


def _split_description(issue: JiraIssue, desc: str) -> None:
    """导出里的描述是整段 wiki 文本：按段落标题拆出验收标准、技术说明、复现与期望。"""
    parsed = parse_issue_text("描述：\n" + (desc or ""))
    issue.description = parsed.description
    issue.acceptance = issue.acceptance or parsed.acceptance
    issue.tech_notes, issue.repro, issue.expected = parsed.tech_notes, parsed.repro, parsed.expected


# ---------------------------------------------------------------- 导出的 CSV / Excel
_COLS = {
    "key": ("问题关键字", "关键字", "issue key", "key", "单号"),
    "summary": ("摘要", "summary", "标题"),
    "issue_type": ("问题类型", "issue type", "类型"),
    "status": ("状态", "status"),
    "priority": ("优先级", "priority"),
    "components": ("模块", "组件", "component/s", "components", "component"),
    "labels": ("标签", "labels", "label"),
    "description": ("描述", "description"),
    "original_h": ("原始预估", "original estimate", "初始预估"),
    "remaining_h": ("剩余预估", "remaining estimate"),
    "spent_h": ("耗费时间", "已用时间", "实际耗时", "time spent", "已登记工时"),
}


def _norm_header(h: str) -> str:
    h = (h or "").strip().lower()
    h = re.sub(r"^(custom field|自定义字段)\s*\((.*)\)$", r"\2", h)
    return h.strip()


def _field_of(header: str) -> str | None:
    h = _norm_header(header)
    if h.startswith("σ") or h.startswith("Σ".lower()):
        return None   # Σ 汇总列（含子任务）不要
    for k, names in _COLS.items():
        if h in names:
            return k
    if "story point" in h or "故事点" in h:
        return "story_points"
    if "验收" in h or "acceptance" in h:
        return "acceptance"
    return None


def _hours(header: str, v) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    n = _num(v)
    if n is None:
        return parse_duration_h(v)
    if re.search(r"小时|\(h\)|（h）|hours?", header, flags=re.I):
        return round(n, 2)
    return round(n / 3600, 2)   # Jira 导出的时间字段是秒


def _rows_from_bytes(data: bytes, name: str = "") -> list[list[str]]:
    if name.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        ws = load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
        return [["" if c is None else str(c) for c in row] for row in ws.iter_rows(values_only=True)]
    for enc in ("utf-8-sig", "gb18030"):
        try:
            return list(csv.reader(io.StringIO(data.decode(enc))))
        except UnicodeDecodeError:
            continue
    return list(csv.reader(io.StringIO(data.decode("utf-8", errors="replace"))))


def parse_export(data: bytes, name: str = "") -> list[JiraIssue]:
    """Jira 筛选结果导出的 CSV（同名列可重复，如多个「模块」）或另存的 Excel → 工单列表。"""
    rows = [r for r in _rows_from_bytes(data, name) if any(str(c).strip() for c in r)]
    if not rows:
        return []
    header = rows[0]
    fields = [_field_of(h) for h in header]
    issues = []
    for row in rows[1:]:
        it = JiraIssue()
        desc = ""
        for h, k, v in zip(header, fields, row):
            v = (v or "").strip()
            if not k or not v:
                continue
            if k in ("components", "labels"):
                getattr(it, k).extend(x.strip() for x in re.split(r"[,，;；]", v) if x.strip())
            elif k == "description":
                desc = v
            elif k == "acceptance":
                it.acceptance = _items(clean_markup(v))
            elif k in ("original_h", "remaining_h", "spent_h"):
                setattr(it, k, _hours(h, v))
            elif k == "story_points":
                it.story_points = _num(v)
            else:
                setattr(it, k, v)
        _split_description(it, desc)
        if it.summary or it.key:
            issues.append(it)
    return issues


def load_export(path: str | Path) -> list[JiraIssue]:
    p = Path(path)
    return parse_export(p.read_bytes(), p.name)

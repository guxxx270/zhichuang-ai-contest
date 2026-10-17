"""Jira 需求评估：已写完整的工单 → 技术复杂度、工时、AI 编码工时。数字全由程序算，每一项都给依据。

和「零散需求」那条线的区别：
- 工单已经写清楚了，不再出追问清单，改做质量检查（缺验收标准、没写影响范围、提了性能没给指标…）；
- 复杂度看技术六维：改动规模、涉及系统、数据与口径、接口与性能、测试与验收、风险与依赖；
- 工时 = 类型基线 + 六维加权（规则模型），再用已完成工单的实际耗时校准（团队系数 + 相似工单类比）；
- AI 编码工时只算编码阶段（编码、单测、自测联调、代码评审；缺陷另含定位排查），全流程另列；
- 有故事点 / 原始预估的，和需知的估算对比，偏差大的标出来。
系数均为示例值，按公司 AI 编码工具的实际使用情况校准。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median

from .. import knowledge
from ..jira import HOURS_PER_DAY, JiraIssue
from .tasks import AI_DO, AI_REVIEW, HUMAN

DIMS = ["改动规模", "涉及系统", "数据与口径", "接口与性能", "测试与验收", "风险与依赖"]
WEIGHTS = {"改动规模": 0.6, "涉及系统": 0.4, "数据与口径": 0.4, "接口与性能": 0.35, "测试与验收": 0.25, "风险与依赖": 0.4}   # 每高 1 分加的人天
KIND_BASE = {"缺陷": 0.6, "任务": 1.0, "需求": 1.5, "改造": 2.5}       # 类型基线（人天）
KIND_SCALE = {"缺陷": 0.5, "任务": 0.8, "需求": 1.0, "改造": 1.2}      # 六维加权对该类型的放大
CODING = "编码"

_TRADING = re.compile(r"下单|报单|撤单|柜台|指令|事前风控|阻断|拒单")
_CUSTOMER = re.compile(r"客户|投资者|资金账号|脱敏|适当性|合规|监管|报送")
_EXTERNAL = re.compile(r"第三方|托管行|外部接口|外部系统|其他团队|依赖|抓取")   # 「按交易所日历」不算外部依赖
_ONLINE = re.compile(r"线上|生产|紧急|P0|故障")
_FUTURES_TERMS = ("结算", "夜盘", "交易日", "节假日", "换月", "主力合约", "限仓", "保证金", "涨跌停", "复权", "净值", "交割")
_EDGE_TERMS = ("夜盘", "节假日", "跨月", "换月", "涨跌停", "极端", "断线")
_PERF_METRIC = re.compile(r"P9[59]|\d+\s*(?:ms|毫秒|秒)|QPS|TPS|并发|吞吐")
_PERF_VAGUE = re.compile(r"要快|尽快返回|性能好|别卡|流畅")
_SMALL = re.compile(r"新增字段|增加字段|加字段|加一列|增加.{0,8}列|新增.{0,8}列|改样式|改文案|格式|显示|配置项|调整阈值|千分位|小数")
_LARGE = re.compile(r"新服务|新模块|新系统|重构|迁移|升级|改造|全量|批量|自动抓取|自动解析")
_BOILER = re.compile(r"页面|报表|导出|Excel|CRUD|字段|配置|看板|筛选|列")


@dataclass
class CodeTask:
    name: str
    phase: str          # 设计 / 编码 / 测试 / 上线
    who: str            # AI 能做 / AI 做人审 / 人必须做
    share: float
    coef: float         # AI 编码人天 = 传统人天 × coef
    note: str
    trad_days: float = 0.0
    ai_days: float = 0.0


@dataclass
class Calibration:
    factor: float = 1.0
    days_per_sp: float | None = None
    samples: list[tuple[JiraIssue, float]] = field(default_factory=list)   # (已完成工单, 规则模型人天)
    note: str = "没有已完成工单可校准，按规则模型"


@dataclass
class Assessment:
    issue: JiraIssue
    kind: str
    dims: dict[str, int]
    reasons: dict[str, str]
    level: str
    model_days: float
    mid: float
    low: float
    high: float
    calib_note: str
    tasks: list[CodeTask]
    coding_trad: float
    coding_ai: float
    full_ai: float
    risks: list[tuple[str, str]]
    confidence: str
    confidence_reason: str
    similar: list[tuple[JiraIssue, float]]
    jira_days: float | None = None
    jira_basis: str = ""
    deviation: float | None = None
    deviation_note: str = ""

    @property
    def total(self) -> int:
        return sum(self.dims.values())

    @property
    def coding_saving_pct(self) -> int:
        return round(100 * (1 - self.coding_ai / self.coding_trad)) if self.coding_trad else 0

    @property
    def full_saving_pct(self) -> int:
        return round(100 * (1 - self.full_ai / self.mid)) if self.mid else 0


def _clip(v: int) -> int:
    return max(1, min(5, v))


def kind_of(issue: JiraIssue) -> str:
    t = (issue.issue_type or "").lower()
    if re.search(r"bug|缺陷|故障", t):
        return "缺陷"
    if re.search(r"improvement|改进|优化|重构|技术债", t) or (re.search(r"task|任务", t) and _LARGE.search(issue.summary or "")):
        return "改造"
    if re.search(r"story|故事|需求|feature|新功能|epic|史诗", t):
        return "需求"
    if re.search(r"task|任务", t):
        return "任务"
    return "缺陷" if issue.repro or issue.expected else "需求"


def _without_systems(text: str) -> str:
    """去掉系统名再找业务口径词：「结算系统」里的「结算」不算结算口径。"""
    for s in knowledge.systems():
        text = text.replace(s.name, " ")
    return text


def _systems_in(issue: JiraIssue) -> list[str]:
    text = issue.text
    found = [s.name for s in knowledge.systems() if s.name in text]
    for c in issue.components:
        if c and c not in found:
            found.append(c)
    return found


def score_dims(issue: JiraIssue, kind: str) -> tuple[dict[str, int], dict[str, str]]:
    text = issue.text
    ac = len(issue.acceptance)
    # 改动规模
    scope = {"缺陷": 1, "任务": 2, "需求": 2, "改造": 3}[kind]
    small, large = bool(_SMALL.search(text)), bool(_LARGE.search(text))
    scope += (1 if large else 0) - (1 if small and not large else 0) + (1 if ac >= 5 else 0) + (1 if ac >= 8 else 0)
    if re.search(r"epic|史诗", issue.issue_type or "", re.I):
        scope += 2
    scope_r = f"{kind}" + (f"；验收 {ac} 条" if ac else "") + ("；以加字段 / 改显示为主" if small and not large else "") + ("；含新建 / 重构 / 批量类改动" if large else "")
    # 涉及系统
    systems = _systems_in(issue)
    cross = bool(re.search(r"对接|联调|调用|同步给|推送给|提供.{0,6}接口", text))
    sys_n = len(systems)
    sysd = (1 if sys_n <= 1 else 2 if sys_n == 2 else 3 if sys_n == 3 else 4) + (1 if cross else 0) + (1 if _EXTERNAL.search(text) else 0)
    sys_r = ("、".join(systems[:4]) if systems else "未写明模块 / 系统") + ("；需跨系统对接" if cross else "") + ("；有外部依赖" if _EXTERNAL.search(text) else "")
    # 数据与口径
    terms = [t for t in _FUTURES_TERMS if t in _without_systems(text)]
    plain = re.sub(r"(?:不|无需|无须|不涉及|不用)(?:改|动|涉及|做)?(?:表结构|DDL|数据迁移|历史数据)", "", text)   # 「不改表结构」不算
    ddl = bool(re.search(r"表结构|DDL|新增.{0,4}表|数据迁移|回填|回刷|历史数据", plain))
    data = 1 + min(3, len(terms)) + (1 if ddl else 0)
    data_r = ("期货口径：" + "、".join(terms[:4]) if terms else "无特殊业务口径") + ("；含表结构 / 历史数据处理" if ddl else "")
    # 接口与性能
    metric = bool(_PERF_METRIC.search(text))
    realtime = bool(re.search(r"实时|盘中|秒级|毫秒", text))
    bigdata = bool(re.search(r"大数据量|百万|千万|全量|全品种|逐客户", text))
    perf = 1 + (2 if metric else 0) + (1 if realtime else 0) + (1 if bigdata else 0)
    perf_r = "，".join(x for x in (("有明确性能指标" if metric else ""), ("实时 / 盘中" if realtime else ""), ("数据量大" if bigdata else ""))
                      if x) or "无性能要求"
    # 测试与验收
    test_cues = bool(re.search(r"单元测试|单测|回归|压测|比对|对账|并行", text))
    edges = [t for t in _EDGE_TERMS if t in text]
    test = (1 if ac <= 2 else 2 if ac <= 4 else 3 if ac <= 7 else 4) + (1 if test_cues else 0) + (1 if len(edges) >= 2 else 0)
    test_r = f"验收 {ac} 条" + ("；明确要求单测 / 回归 / 比对" if test_cues else "") + (f"；边界场景：{'、'.join(edges)}" if edges else "")
    # 风险与依赖
    trading, customer = bool(_TRADING.search(text)), bool(_CUSTOMER.search(text))
    online = kind == "缺陷" and bool(_ONLINE.search(text + " ".join(issue.labels) + (issue.priority or "")))
    no_ac = kind in ("需求", "改造") and not ac
    risk = 1 + (2 if trading else 0) + (1 if customer else 0) + (1 if _EXTERNAL.search(text) else 0) + (1 if online else 0) + (1 if no_ac else 0)
    risk_r = "，".join(x for x in (("触及交易链路" if trading else ""), ("涉及客户数据 / 合规" if customer else ""),
                                   ("有外部依赖" if _EXTERNAL.search(text) else ""), ("线上问题" if online else ""),
                                   ("缺验收标准" if no_ac else "")) if x) or "常规"
    dims = {"改动规模": _clip(scope), "涉及系统": _clip(sysd), "数据与口径": _clip(data), "接口与性能": _clip(perf),
            "测试与验收": _clip(test), "风险与依赖": _clip(risk)}
    reasons = {"改动规模": scope_r, "涉及系统": sys_r, "数据与口径": data_r, "接口与性能": perf_r, "测试与验收": test_r, "风险与依赖": risk_r}
    return dims, reasons


def model_days(dims: dict[str, int], kind: str) -> float:
    return KIND_BASE[kind] + KIND_SCALE[kind] * sum(WEIGHTS[d] * (dims[d] - 1) for d in DIMS)


def _terms(issue: JiraIssue) -> set[str]:
    text = issue.text
    vocab = [s.name for s in knowledge.systems()] + list(_FUTURES_TERMS) + [
        "接口", "字段", "页面", "报表", "日报", "周报", "导出", "Excel", "推送", "提醒", "权限", "日历", "行情", "订阅", "重连",
        "脱敏", "看板", "筛选", "配置", "阈值", "公告", "变动", "风险度", "持仓"]
    return {w for w in vocab if w in text} | set(issue.components)


def similarity(a: JiraIssue, b: JiraIssue) -> float:
    ta, tb = _terms(a), _terms(b)
    jac = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    same_kind = kind_of(a) == kind_of(b)
    comp = bool(set(a.components) & set(b.components))
    return round(0.25 * same_kind + 0.2 * comp + 0.55 * jac, 2)


def calibrate(done: list[JiraIssue]) -> Calibration:
    """用已完成工单（有实际耗时）算团队系数：实际人天 ÷ 规则模型人天 的中位数；以及每个故事点折多少人天。"""
    samples = []
    for it in done:
        if it.done and it.spent_days:
            k = kind_of(it)
            samples.append((it, model_days(score_dims(it, k)[0], k)))
    if len(samples) < 3:
        return Calibration(samples=samples, note=f"已完成工单只有 {len(samples)} 条，不足 3 条，按规则模型（不校准）")
    factor = max(0.5, min(2.0, median(it.spent_days / m for it, m in samples)))
    sp = [it.spent_days / it.story_points for it, _ in samples if it.story_points]
    dps = round(median(sp), 2) if len(sp) >= 3 else None
    return Calibration(round(factor, 2), dps, samples,
                       f"按 {len(samples)} 条已完成工单的实际耗时校准：团队系数 ×{factor:.2f}"
                       + (f"，1 个故事点 ≈ {dps} 人天" if dps else ""))


def _tasks(issue: JiraIssue, kind: str, dims: dict[str, int]) -> list[CodeTask]:
    text = issue.text
    trading = bool(_TRADING.search(text))

    def who(c: float) -> str:
        return AI_DO if c <= 0.45 else AI_REVIEW if c <= 0.75 else HUMAN

    if kind == "缺陷":
        spec = [
            ("问题定位与根因分析", CODING, 0.35, 0.65, "AI 读日志、读代码给怀疑点，结论要人确认"),
            ("修复编码", CODING, 0.20, 0.8 if trading else 0.5, "交易链路上的修复 AI 只写草稿" if trading else "改动点明确，AI 直接给补丁"),
            ("补充单元测试", CODING, 0.10, 0.35, "按复现步骤生成回归用例"),
            ("代码评审与修改", CODING, 0.05, 0.7, "AI 先审一遍，人做最终评审"),
            ("回归测试", "测试", 0.20, 0.6, "AI 出回归用例，跑与判定靠人"),
            ("发布与线上验证", "上线", 0.10, 0.9, "按变更流程走，基本省不了"),
        ]
    else:
        code = 0.45 - (0.05 if _BOILER.search(text) else 0) + (0.1 if dims["数据与口径"] >= 3 else 0)
        code = 0.8 if trading else max(0.35, min(0.85, code))
        link = 0.75 + (0.1 if dims["涉及系统"] >= 3 else 0)
        design_share = 0.12 if kind == "改造" else 0.10
        code_share = 0.45 if kind == "改造" else 0.40
        spec = [
            ("方案与技术设计", "设计", design_share, 0.7, "AI 出方案草稿与接口定义，人拍板"),
            ("编码实现", CODING, code_share, code,
             "交易链路：AI 写草稿，逐行人审" if trading else ("业务口径多，AI 生成后要逐条核对口径" if dims["数据与口径"] >= 3
                                                   else "标准化代码为主（接口 / 页面 / 报表 / 配置），AI 生成为主")),
            ("单元测试", CODING, 0.12, 0.35, "AI 按验收标准与边界场景（夜盘、节假日、换月）生成"),
            ("自测与联调", CODING, 0.14, link, "跨系统联调靠人，AI 帮写联调脚本与造数" if dims["涉及系统"] >= 3 else "AI 帮造测试数据，自测靠人"),
            ("代码评审与修改", CODING, 0.06, 0.7, "AI 先审一遍，人做最终评审"),
            ("提测与回归", "测试", 0.12, 0.6, "AI 出测试用例，执行与判定靠人"),
            ("发布上线与验证", "上线", 0.06, 0.9, "按变更流程走，基本省不了"),
        ]
    return [CodeTask(n, ph, who(c), sh, round(c, 2), note) for n, ph, sh, c, note in spec]


def _risks(issue: JiraIssue, kind: str, mid: float) -> list[tuple[str, str]]:
    text = issue.text
    ac = len(issue.acceptance)
    out: list[tuple[str, str]] = []
    if kind in ("需求", "改造") and not ac:
        out.append(("高", "没有验收标准：做到什么程度算完不清楚，估算区间已放宽"))
    elif kind in ("需求", "改造") and ac <= 2:
        out.append(("中", f"验收标准只有 {ac} 条，可能没覆盖边界场景"))
    elif kind == "任务" and not ac:
        out.append(("中", "没有验收标准：做到什么程度算完不清楚"))
    if kind == "缺陷" and not (issue.repro and issue.expected):
        out.append(("中", "缺陷没写全复现步骤 / 期望结果，定位时间难估"))
    if not _systems_in(issue):
        out.append(("中", "没写涉及的模块 / 系统，影响范围不明"))
    if _TRADING.search(text):
        out.append(("高", "触及交易链路：须走变更评审与下单回归，编码只按「AI 做人审」折算"))
    if _CUSTOMER.search(text):
        out.append(("中", "涉及客户数据：注意脱敏与权限，测试数据不能用真实客户"))
    if _PERF_VAGUE.search(text) and not _PERF_METRIC.search(text):
        out.append(("中", "提了性能但没给指标（如 P99 ≤ 200ms）"))
    ac_text = " ".join(issue.acceptance)
    missing = [t for t in ("夜盘", "节假日", "换月", "结算") if t in _without_systems(text) and t not in ac_text]
    if missing and ac:
        out.append(("中", f"提到{'、'.join(missing)}，但验收标准里没有对应场景"))
    if re.search(r"epic|史诗", issue.issue_type or "", re.I):
        out.append(("中", "史诗（Epic）粒度太大，建议先拆成故事再估"))
    elif mid > 10:
        out.append(("中", f"单条估到 {mid} 人天，建议拆分"))
    return out


def assess(issue: JiraIssue, calib: Calibration | None = None) -> Assessment:
    calib = calib or Calibration()
    kind = kind_of(issue)
    dims, reasons = score_dims(issue, kind)
    m = model_days(dims, kind)
    base = m * calib.factor
    # 只和同类型的已完成工单类比（缺陷不拿需求比）；史诗粒度太大，不做类比
    epic = bool(re.search(r"epic|史诗", issue.issue_type or "", re.I))
    sims = sorted(((it, similarity(issue, it)) for it, _ in calib.samples
                   if it.key != issue.key and kind_of(it) == kind and not epic), key=lambda x: -x[1])
    sims = [(it, s) for it, s in sims if s >= 0.35][:3]
    if sims and sims[0][1] >= 0.5:
        knn = sum(it.spent_days * s for it, s in sims) / sum(s for _, s in sims)
        mid = 0.5 * knn + 0.5 * base
    else:
        mid = base
    mid = round(max(0.3, mid), 1)
    risks = _risks(issue, kind, mid)
    n_hi = sum(1 for lv, _ in risks if lv == "高")
    n_md = sum(1 for lv, _ in risks if lv == "中")
    low, high = round(mid * 0.8, 1), round(mid * (1.25 + 0.1 * n_hi + 0.05 * n_md), 1)

    tasks = _tasks(issue, kind, dims)
    s = sum(t.share for t in tasks)
    for t in tasks:
        t.trad_days = round(mid * t.share / s, 2)
        t.ai_days = round(t.trad_days * t.coef, 2)
    coding_trad = round(sum(t.trad_days for t in tasks if t.phase == CODING), 1)
    coding_ai = round(sum(t.ai_days for t in tasks if t.phase == CODING), 1)
    full_ai = round(sum(t.ai_days for t in tasks), 1)

    total = sum(dims.values())
    level = "低" if total <= 10 else "中" if total <= 14 else "高" if total <= 19 else "很高"
    ac = len(issue.acceptance)
    has_sim = bool(sims and sims[0][1] >= 0.5)
    if (ac >= 3 or (kind == "缺陷" and issue.repro and issue.expected)) and has_sim and len(calib.samples) >= 3:
        conf, why = "高", f"工单写得完整，且有相似的已完成工单（{sims[0][0].key}，相似度 {sims[0][1]:.0%}）可参照"
    elif ac or has_sim or (kind == "缺陷" and issue.repro):
        conf, why = "中", ("有相似已完成工单可参照" if has_sim else "没有足够相似的已完成工单") + ("，验收标准较完整" if ac >= 3 else "，验收标准不全")
    else:
        conf, why = "低", "缺验收标准，也没有相似的已完成工单；建议补全工单再报数"

    jira_days, basis = None, ""
    if issue.original_h:
        jira_days, basis = round(issue.original_h / HOURS_PER_DAY, 1), f"原始预估 {issue.original_h:g} 小时"
    elif issue.story_points and calib.days_per_sp:
        jira_days = round(issue.story_points * calib.days_per_sp, 1)
        basis = f"{issue.story_points:g} 个故事点 × {calib.days_per_sp} 人天 / 点"
    dev = note = None
    if jira_days:
        dev = round((mid - jira_days) / jira_days, 2)
        note = (f"原估偏乐观：需知估得多 {dev:+.0%}" if dev >= 0.4 else f"原估偏保守：需知估得少 {dev:+.0%}" if dev <= -0.4
                else f"与原估基本一致（{dev:+.0%}）")
    return Assessment(issue, kind, dims, reasons, level, round(m, 1), mid, low, high, calib.note, tasks, coding_trad, coding_ai,
                      full_ai, risks, conf, why, sims, jira_days, basis, dev, note or "")


def assess_many(issues: list[JiraIssue], history: list[JiraIssue] | None = None) -> tuple[list[Assessment], Calibration]:
    """批量评估：同批里已完成的工单 + 历史工单一起当校准样本；已完成的工单本身也评一遍（可看估得准不准）。"""
    pool = {it.key or it.summary: it for it in (history or []) if it.done}
    pool.update({it.key or it.summary: it for it in issues if it.done})
    calib = calibrate(list(pool.values()))
    return [assess(it, calib) for it in issues], calib

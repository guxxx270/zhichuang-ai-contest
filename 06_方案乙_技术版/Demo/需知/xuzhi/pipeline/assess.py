"""Jira 需求评估：已写完整的工单 → 技术复杂度、工时、AI 编码工时。数字全由程序算，每一项都给依据。

和「零散需求」那条线的区别：
- 工单已经写清楚了，不再出追问清单，改做质量检查（缺验收标准、没写影响范围、提了性能没给指标…）；
- 复杂度看技术六维：改动规模、涉及系统、数据与口径、接口与性能、测试与验收、风险与依赖；
- 工时 = 类型基线 + 六维加权（规则模型），再用已完成工单的实际耗时校准（团队系数 + 相似工单类比）；
- AI 编码工时只算编码阶段（编码、单测、自测联调、代码评审；缺陷另含定位排查），全流程另列；
- 有故事点 / 原始预估的，和需知的估算对比，偏差大的标出来；只有历史回测（留一法）里需知比 Jira 原估准，才说原估偏乐观 / 偏保守。
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

_TRADING = re.compile(r"下单|报单|撤单|柜台|(?:交易|报单|下单)指令|事前风控|阻断|拒单")   # 「企微 /摘要 指令」不是交易指令
_CUSTOMER = re.compile(r"客户(?!端|经理|版)|投资者|资金账号|脱敏|适当性|合规|监管|报送")   # 客户端、客户经理不是客户数据
_CLIENT_VER = re.compile(r"客户版")   # 对外的客户版：不一定碰客户数据，但要合规审阅，风险照样算
_EXTERNAL = re.compile(r"第三方|托管行|外部接口|外部系统|其他团队|依赖|抓取")   # 「按交易所日历」不算外部依赖
# 否定说法：「不修改表结构」「表结构不变」「无外部依赖」「不重构、不迁移」——匹配规模 / 依赖 / 表结构之前先删掉；
# 「不得不重构」「不能不迁移」是双重否定（必须做），不删
_NEGATED = re.compile(r"(?<!不得)(?<!不能)(?:不需要|不涉及|无需|无须|不用|不|无)\s*(?:修改|改动|变更|改|动|做|涉及)?\s*"
                      r"(?:外部依赖|依赖(?:其他系统|外部系统|外部接口|第三方|托管行|其他团队)?|表结构(?:变更|变化|改动)?|DDL"
                      r"|重构|(?:数据)?迁移(?:历史数据)?|回填|回刷|历史数据)|表结构不变")
_ONLINE = re.compile(r"线上|生产|紧急|P0|故障")
_FUTURES_TERMS = ("结算", "夜盘", "交易日", "节假日", "换月", "主力合约", "限仓", "保证金", "涨跌停", "复权", "净值", "交割")
_EDGE_TERMS = ("夜盘", "节假日", "跨月", "换月", "涨跌停", "极端", "断线")
_PERF_METRIC = re.compile(r"P9[59]|\d+\s*(?:ms|毫秒|秒)|QPS|TPS|并发|吞吐")
_PERF_VAGUE = re.compile(r"要快|尽快返回|性能好|别卡|流畅")
_SMALL = re.compile(r"新增字段|增加字段|加字段|加一列|增加.{0,8}列|新增.{0,8}列|改样式|改文案|文案|格式|显示|配置项|调整阈值|千分位|小数")
_LARGE = re.compile(r"新服务|新模块|新系统|重构|迁移|升级|改造|全量|批量|自动抓取|自动解析")
_BOILER = re.compile(r"页面|报表|导出|Excel|CRUD|字段|配置|看板|筛选|列")
DEVIATION_FLAG = 0.4   # 与原估差 ≥ 40% 才标出来
BACKTEST_MAX = 200     # 历史回测最多取的条数


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
    # 历史回测（留一法）：已完成且有原估的工单逐条「用其余工单校准再估」，和 Jira 原估比谁离实际近
    backtest_mape: float | None = None    # 需知估 vs 实际的平均相对误差
    orig_mape: float | None = None        # 同一批工单 Jira 原估 vs 实际
    backtest_n: int = 0

    @property
    def beats_original(self) -> bool:
        """回测至少 3 条、且需知误差低于原估，才有底气说原估偏乐观 / 偏保守。"""
        return self.backtest_n >= 3 and self.backtest_mape is not None and self.orig_mape is not None \
            and self.backtest_mape < self.orig_mape

    @property
    def backtest_line(self) -> str:
        if not self.backtest_n:
            return ""
        return f"历史回测（留一法，{self.backtest_n} 条）：需知 MAPE {self.backtest_mape:.0%} / Jira 原估 {self.orig_mape:.0%}"


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
    # 取整前的编码阶段人工 / AI 与全流程 AI：省时百分比按它们算（小单取整后 0.56→0.32 会显示成 0.6→0.3、省 50%）
    coding_trad_raw: float | None = None
    coding_ai_raw: float | None = None
    full_ai_raw: float | None = None

    @property
    def total(self) -> int:
        return sum(self.dims.values())

    @property
    def coding_saving_pct(self) -> int:
        trad, ai = (self.coding_trad_raw, self.coding_ai_raw) if self.coding_trad_raw else (self.coding_trad, self.coding_ai)
        return round(100 * (1 - ai / trad)) if trad else 0

    @property
    def full_saving_pct(self) -> int:
        ai = self.full_ai_raw if self.full_ai_raw is not None else self.full_ai
        return round(100 * (1 - ai / self.mid)) if self.mid else 0


def _clip(v: int) -> int:
    return max(1, min(5, v))


def is_epic(issue: JiraIssue) -> bool:
    return bool(re.search(r"epic|史诗", issue.issue_type or "", re.I))


def _strip_negated(text: str) -> str:
    return _NEGATED.sub(" ", text)


def kind_of(issue: JiraIssue) -> str:
    t = (issue.issue_type or "").lower()
    if re.search(r"bug|缺陷|故障", t):
        return "缺陷"
    if re.search(r"improvement|改进|优化", t):
        # 改进 / 优化里也有「按钮文案改成保存」这种 2 小时的小单：只有明显是小改动才按任务算，其余仍按改造
        text = issue.text
        small = _SMALL.search(text) and not _LARGE.search(_strip_negated(text)) and len(issue.acceptance) <= 2
        return "任务" if small else "改造"
    if re.search(r"重构|技术债", t) or (re.search(r"task|任务", t) and _LARGE.search(_strip_negated(issue.summary or ""))):
        return "改造"
    if re.search(r"story|故事|需求|feature|新功能|epic|史诗", t):
        return "需求"
    if re.search(r"task|任务", t):
        return "任务"
    return "缺陷" if issue.repro else "需求"   # 没写类型：有复现步骤才是缺陷，「期望：每天 8 点前推送」是需求


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
    plain = _strip_negated(text)   # 「不重构」「无外部依赖」「表结构不变」不算
    ac = len(issue.acceptance)
    # 改动规模
    scope = {"缺陷": 1, "任务": 2, "需求": 2, "改造": 3}[kind]
    small, large = bool(_SMALL.search(text)), bool(_LARGE.search(plain))
    scope += (1 if large else 0) - (1 if small and not large else 0) + (1 if ac >= 5 else 0) + (1 if ac >= 8 else 0)
    if is_epic(issue):
        scope += 2
    scope_r = f"{kind}" + (f"；验收 {ac} 条" if ac else "") + ("；以加字段 / 改显示为主" if small and not large else "") + ("；含新建 / 重构 / 批量类改动" if large else "")
    # 涉及系统
    systems = _systems_in(issue)
    cross = bool(re.search(r"对接|联调|调用(?!方)|同步给|推送给|提供.{0,6}接口", text))   # 「老调用方不受影响」不是要对接
    external = bool(_EXTERNAL.search(plain))
    sys_n = len(systems)
    sysd = (1 if sys_n <= 1 else 2 if sys_n == 2 else 3 if sys_n == 3 else 4) + (1 if cross else 0) + (1 if external else 0)
    sys_r = ("、".join(systems[:4]) if systems else "未写明模块 / 系统") + ("；需跨系统对接" if cross else "") + ("；有外部依赖" if external else "")
    # 数据与口径
    terms = [t for t in _FUTURES_TERMS if t in _without_systems(text)]
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
    trading, customer, client_ver = bool(_TRADING.search(text)), bool(_CUSTOMER.search(text)), bool(_CLIENT_VER.search(text))
    online = kind == "缺陷" and bool(_ONLINE.search(text + " ".join(issue.labels) + (issue.priority or "")))
    no_ac = kind in ("需求", "改造") and not ac
    risk = 1 + (2 if trading else 0) + (1 if customer or client_ver else 0) + (1 if external else 0) + (1 if online else 0) + (1 if no_ac else 0)
    risk_r = "，".join(x for x in (("触及交易链路" if trading else ""),
                                   ("涉及客户数据 / 合规" if customer else "有客户版，需合规审阅" if client_ver else ""),
                                   ("有外部依赖" if external else ""), ("线上问题" if online else ""),
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


def _match(a: JiraIssue, ta: set[str], b: JiraIssue) -> tuple[float, set[str]]:
    """(相似度, 共享的业务名词)。候选已按同类型筛过，不再给「同类型」加分；系统名 / 模块名不算业务名词。"""
    tb = _terms(b)
    jac = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    comp = bool(set(a.components) & set(b.components))
    names = {s.name for s in knowledge.systems()} | set(a.components) | set(b.components)
    return round(0.25 * comp + 0.75 * jac, 2), (ta & tb) - names


def similarity(a: JiraIssue, b: JiraIssue) -> float:
    return _match(a, _terms(a), b)[0]


def _samples(done: list[JiraIssue]) -> list[tuple[JiraIssue, float]]:
    """可当校准样本的已完成工单：有实际耗时、不是史诗（史诗粒度太大，不拿来算团队系数，也不拿来类比）。"""
    out = []
    for it in done:
        if it.done and it.spent_days and not is_epic(it):
            k = kind_of(it)
            out.append((it, model_days(score_dims(it, k)[0], k)))
    return out


def _calibration(samples: list[tuple[JiraIssue, float]]) -> Calibration:
    if len(samples) < 3:
        return Calibration(samples=samples, note=f"已完成工单只有 {len(samples)} 条，不足 3 条，按规则模型（不校准）")
    factor = max(0.5, min(2.0, median(it.spent_days / m for it, m in samples)))
    sp = [it.spent_days / it.story_points for it, _ in samples if it.story_points]
    dps = round(median(sp), 2) if len(sp) >= 3 else None
    return Calibration(round(factor, 2), dps, samples,
                       f"按 {len(samples)} 条已完成工单的实际耗时校准：团队系数 ×{factor:.2f}"
                       + (f"，1 个故事点 ≈ {dps} 人天" if dps else ""))


def _leave_one_out(samples: list[tuple[JiraIssue, float]], ids: set[int] | None = None) -> dict[int, "Assessment"]:
    """留一法：每条已完成工单用「其余工单」校准后再估。回测用它，批量表里的已完成行也用它——不拿自己校准自己。
    ids 给定时只估这几条（按 id(工单)）。"""
    return {id(it): assess(it, _calibration(samples[:i] + samples[i + 1:]))
            for i, (it, _) in enumerate(samples) if ids is None or id(it) in ids}


def _backtest_loo(samples: list[tuple[JiraIssue, float]]) -> dict[int, "Assessment"]:
    """回测用的留一法：只在最后 BACKTEST_MAX 条（一本账、本次批量排在后面，较新）之间互相留一。
    留一法是 n² 的，每条都拿全部历史校准的话，历史 3000 条时粘贴一条要等两三秒；这样历史导出再多，回测的工作量也封顶。
    样本不超过 BACKTEST_MAX 条时，就是全部样本互相留一。"""
    return _leave_one_out(samples[-BACKTEST_MAX:])


def _mape(pairs: list[tuple[float, float]]) -> float:
    return round(sum(abs(est - act) / act for est, act in pairs) / len(pairs), 3)


def _backtest(samples, loo) -> tuple[float | None, float | None, int]:
    rows = [(loo[id(it)].mid, it.original_h / HOURS_PER_DAY, it.spent_days)
            for it, _ in samples[-BACKTEST_MAX:] if it.original_h and id(it) in loo]
    if not rows:
        return None, None, 0
    return _mape([(m, act) for m, _, act in rows]), _mape([(o, act) for _, o, act in rows]), len(rows)


def backtest(pool: list[JiraIssue]) -> tuple[float | None, float | None, int]:
    """历史回测：最后 BACKTEST_MAX 条已完成工单之间留一法估算，有原估的拿来比。
    返回 (需知 MAPE, Jira 原估 MAPE, 条数)，没有可回测的返回 (None, None, 0)。"""
    samples = _samples(pool)
    return _backtest(samples, _backtest_loo(samples))


def _with_backtest(samples, loo) -> Calibration:
    calib = _calibration(samples)
    calib.backtest_mape, calib.orig_mape, calib.backtest_n = _backtest(samples, loo)
    return calib


def calibrate(done: list[JiraIssue]) -> Calibration:
    """用已完成工单（有实际耗时）算团队系数：实际人天 ÷ 规则模型人天 的中位数；以及每个故事点折多少人天。
    同时做历史回测，决定和原估对比时能不能说「偏乐观 / 偏保守」。"""
    samples = _samples(done)
    return _with_backtest(samples, _backtest_loo(samples))


def _tasks(issue: JiraIssue, kind: str, dims: dict[str, int]) -> list[CodeTask]:
    text = issue.text
    trading = bool(_TRADING.search(text))

    def who(c: float) -> str:
        return AI_DO if c <= 0.45 else AI_REVIEW if c <= 0.75 else HUMAN

    # 第 6 项是分工覆盖：交易链路的编码系数 0.8 按档位会落到「人必须做」，但口径是「AI 写草稿、逐行人审」
    review = AI_REVIEW if trading else None
    if kind == "缺陷":
        spec = [
            ("问题定位与根因分析", CODING, 0.35, 0.65, "AI 读日志、读代码给怀疑点，结论要人确认"),
            ("修复编码", CODING, 0.20, 0.8 if trading else 0.5, "交易链路上的修复 AI 只写草稿" if trading else "改动点明确，AI 直接给补丁", review),
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
                                                   else "标准化代码为主（接口 / 页面 / 报表 / 配置），AI 生成为主"), review),
            ("单元测试", CODING, 0.12, 0.35, "AI 按验收标准与边界场景（夜盘、节假日、换月）生成"),
            ("自测与联调", CODING, 0.14, link, "跨系统联调靠人，AI 帮写联调脚本与造数" if dims["涉及系统"] >= 3 else "AI 帮造测试数据，自测靠人"),
            ("代码评审与修改", CODING, 0.06, 0.7, "AI 先审一遍，人做最终评审"),
            ("提测与回归", "测试", 0.12, 0.6, "AI 出测试用例，执行与判定靠人"),
            ("发布上线与验证", "上线", 0.06, 0.9, "按变更流程走，基本省不了"),
        ]
    return [CodeTask(n, ph, (w[0] if w and w[0] else who(c)), sh, round(c, 2), note) for n, ph, sh, c, note, *w in spec]


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
    elif _CLIENT_VER.search(text):
        out.append(("中", "有对外的客户版：上线前按合规要求审阅对客字段与口径"))
    if _PERF_VAGUE.search(text) and not _PERF_METRIC.search(text):
        out.append(("中", "提了性能但没给指标（如 P99 ≤ 200ms）"))
    ac_text = " ".join(issue.acceptance)
    missing = [t for t in ("夜盘", "节假日", "换月", "结算") if t in _without_systems(text) and t not in ac_text]
    if missing and ac:
        out.append(("中", f"提到{'、'.join(missing)}，但验收标准里没有对应场景"))
    if is_epic(issue):
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
    # 只和同类型的已完成工单类比（缺陷不拿需求比）；史诗粒度太大，不做类比（样本里也没有史诗）。
    # 至少共享 2 个业务名词才算相似：只共享一个「风控系统」不能拿来类比，也撑不起「置信度 高」
    sims = []
    if not is_epic(issue):
        ta = _terms(issue)
        for it, _ in calib.samples:
            if it.key != issue.key and kind_of(it) == kind:
                s, shared = _match(issue, ta, it)
                if s >= 0.35 and len(shared) >= 2:
                    sims.append((it, s))
    sims = sorted(sims, key=lambda x: -x[1])[:3]
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
    raw = [(t, mid * t.share / s) for t in tasks]
    for t, trad in raw:
        t.trad_days = round(trad, 2)
        t.ai_days = round(t.trad_days * t.coef, 2)
    coding_trad_raw = sum(trad for t, trad in raw if t.phase == CODING)
    coding_ai_raw = sum(trad * t.coef for t, trad in raw if t.phase == CODING)
    full_ai_raw = sum(trad * t.coef for t, trad in raw)
    coding_trad, coding_ai, full_ai = round(coding_trad_raw, 1), round(coding_ai_raw, 1), round(full_ai_raw, 1)

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
        note = _deviation_note(dev, jira_days, mid, calib, "原估" if issue.original_h else "点数折算")
    return Assessment(issue, kind, dims, reasons, level, round(m, 1), mid, low, high, calib.note, tasks, coding_trad, coding_ai,
                      full_ai, risks, conf, why, sims, jira_days, basis, dev, note or "",
                      coding_trad_raw, coding_ai_raw, full_ai_raw)


def _deviation_note(dev: float, jira_days: float, mid: float, calib: Calibration, what: str) -> str:
    """差得多也不轻易下结论：小单（原估不到 1 天、需知估不到 1.5 天）只报差值；
    只有历史回测里需知比 Jira 原估准，才说「原估偏乐观 / 偏保守」，否则写中性的差值并附回测误差。"""
    if abs(dev) < DEVIATION_FLAG:
        return f"与{what}基本一致（{dev:+.0%}）"
    if jira_days < 1 and mid < 1.5:
        return f"与{what}差 {dev:+.0%}"
    if what == "原估" and calib.beats_original:
        return f"原估偏乐观：需知估得多 {dev:+.0%}" if dev > 0 else f"原估偏保守：需知估得少 {dev:+.0%}"
    if what != "原估":
        return f"与{what}差 {dev:+.0%}"
    if calib.backtest_n:
        return f"与原估差 {dev:+.0%}（历史回测：需知误差 {calib.backtest_mape:.0%}，原估 {calib.orig_mape:.0%}）"
    return f"与原估差 {dev:+.0%}（暂无历史回测，不判断偏乐观 / 偏保守）"


def assess_many(issues: list[JiraIssue], history: list[JiraIssue] | None = None) -> tuple[list[Assessment], Calibration]:
    """批量评估：同批里已完成的工单 + 历史工单一起当校准样本。已完成的工单本身也评一遍（可看估得准不准），
    但用留一法：它自己不进自己的校准样本，否则「对照需知估与实际」就是拿答案对答案。"""
    pool = {it.key or it.summary: it for it in (history or []) if it.done}
    pool.update({it.key or it.summary: it for it in issues if it.done})
    samples = _samples(list(pool.values()))
    bt = _backtest_loo(samples)
    # 本批已完成的行用其余全部样本校准；样本不超过 BACKTEST_MAX 条时回测那份就是，不用重算
    loo = bt if len(samples) <= BACKTEST_MAX else _leave_one_out(samples, {id(it) for it in issues})
    calib = _with_backtest(samples, bt)
    return [loo.get(id(it)) or assess(it, calib) for it in issues], calib

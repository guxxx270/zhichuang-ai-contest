"""固定规则参考：已有计算口径的局部修改，按预设工作项估小时。

工时是待代码核对的规则初值，不是历史实测或交付承诺。超出复用边界时回到完整估算。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..textutil import find_changed_indicators
from .tasks import AI_DO, AI_REVIEW, HUMAN, Task


@dataclass(frozen=True)
class ChangeScope:
    indicator: str
    parent_rollup: bool
    price_ratio_in_field: bool


# 只接受单项计算变更；明确新增的系统、接入、迁移等不能套用小改模板。
_EXPANDED_SCOPE = re.compile(
    r"(?:新建|重建|搭建|建设|从零)[^，,。；;\n]{0,12}(?:系统|平台|数据模型|权限模型|页面|接口|审批)"
    r"|(?:新增|新接入|首次接入|接入|对接)[^，,。；;\n]{0,12}(?:第三方|外部|接口|数据源|页面|权限|审批|实时|系统|平台|数据模型)"
    r"|(?:增加|开发)[^，,。；;\n]{0,12}(?:页面|接口|权限|审批|系统|平台|数据模型)"
    r"|(?:回补|回填|迁移|重构|性能改造|实时链路)"
)


def calculation_change_scope(text: str) -> ChangeScope | None:
    indicators = find_changed_indicators(text)
    if len(indicators) != 1 or not re.search(r"报送|报表|日报|周报|现有|已有|目前|当前|原有", text):
        return None
    for match in _EXPANDED_SCOPE.finditer(text):
        prefix = text[max(0, match.start() - 24):match.start()]
        if re.search(r"(?:(?:不得|不能|不可|不必)不|(?:不是|并非)(?:不需要|不用|不必))$", prefix):
            return None
        # 允许“不涉及历史数据迁移”等宾语前置，但不能跨过“但仍需要”等转折。
        if not re.search(r"(?:无需|无须|不需要|不需|不用|不涉及|不要|不)"
                         r"(?:再|额外|进行|任何|相关|历史数据|系统|数据库|数据|新的|一套|全量|的|\s){0,6}$", prefix):
            return None
    return ChangeScope(
        indicator=indicators[0],
        parent_rollup=bool(re.search(r"母产品|母层|父级|父产品|母子", text) and re.search(r"汇总|加总|合计|聚合|求和", text)),
        price_ratio_in_field=any(
            re.search(r"价格比(?:已经|已)?(?:包含|含)(?:在)?[^，,。；;\n]{0,12}字段", clause)
            and not re.search(r"是否|尚未|未包含|没有|可能|待确认|待核对|吗|[？?]", clause)
            for clause in re.split(r"[，,。；;\n]", text)
        ),
    )


@dataclass
class ChangeWork:
    task: Task
    trad_low: float
    trad_high: float
    ai_low: float
    ai_high: float


def build_change_work(scope: ChangeScope) -> list[ChangeWork]:
    """每个区间独立估工作小时；保留精度，到展示时才舍入。"""
    work: list[ChangeWork] = []

    def add(id: str, name: str, phase: str, who: str, note: str,
            trad: tuple[float, float, float], ai: tuple[float, float, float]) -> None:
        tmid, tlow, thigh = trad
        amid, alow, ahigh = ai
        work.append(ChangeWork(Task(id, name, phase, who, tmid, amid / tmid, note,
                                    trad_days=tmid / 8, ai_days=amid / 8), tlow, thigh, alow, ahigh))

    add("I1", "定位现有计算与字段核对", "需求与设计", AI_REVIEW,
        "核对既有调用链、字段含义和取值层级；尚未检查真实代码与字段映射",
        (2, 1, 3), (1.5, 1, 3))
    add("I2", f"修改{scope.indicator}取值与计算", "开发", AI_REVIEW,
        "复用现有字段修改计算与输出映射，人工审查关键数字"
        + ("；价格比已在字段中，不重复相乘" if scope.price_ratio_in_field else "；现有字段的调整口径仍待核对"),
        (3, 1.5, 4), (1, 0.5, 2))
    if scope.parent_rollup:
        add("I3", "母层明细筛选与动态汇总", "开发", AI_REVIEW,
            "按母子关系筛选并汇总本母层明细，新增母产品自动适用；不按产品名称逐个开发",
            (4, 2, 6), (1.5, 1, 3))
    add("I4", "计算回归用例与样本", "测试与比对", AI_DO,
        "覆盖新旧取值、缺值和重复计算"
        + ("，以及筛选条件、多母层归属、未来新增母产品" if scope.parent_rollup else ""),
        (3, 2, 4), (1.5, 1, 2))
    add("I5", "关键数字与报送结果比对", "测试与比对", HUMAN,
        "用可核对样本验证新口径和报送输出，人工复核时间保留",
        (2, 1, 3), (2, 1, 3))
    add("I6", "发布、回滚准备与验收", "上线与培训", HUMAN,
        "沿用既有发布流程并验收；不含排期、审批或等待业务反馈的日历时间",
        (1, 0.5, 2), (1, 0.5, 2))
    return work

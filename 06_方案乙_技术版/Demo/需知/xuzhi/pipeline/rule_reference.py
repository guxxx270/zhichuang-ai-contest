"""旧复杂度规则的隔离参考助手：固定参数、未校准，不供模型生成工时。"""
from __future__ import annotations

import re

from .. import textutil
from .intake import Card
from .tasks import AI_DO, AI_REVIEW, HUMAN, Task

DIMS = ["功能点", "数据接入", "界面", "权限", "性能", "合规"]
WEIGHTS = {"功能点": 1.2, "数据接入": 1.5, "界面": 1.2, "权限": 0.8, "性能": 1.5, "合规": 0.8}


def _clip(v: int) -> int:
    return max(1, min(5, v))


def _bounded(raw: int, detail: str) -> str:
    """分值仍按 1～5 计入工时；依据里写明被上下限截住的加总。"""
    shown = _clip(raw)
    if raw > 5:
        return f"{detail}；加总 {raw}，超过 5，已按上限记 {shown}"
    if raw < 1:
        return f"{detail}；加总 {raw}，低于 1，已按下限记 {shown}"
    return detail


def score_dims(card: Card, text: str) -> tuple[dict[str, int], dict[str, str]]:
    n = len(card.features)
    fp = 1 if n <= 2 else 2 if n <= 4 else 3 if n <= 6 else 4 if n <= 9 else 5
    ds = len(card.data_sources)
    external = bool(re.search(r"现货|第三方|公告|交易所日报|新闻", text))
    # 「公告发布后 30 分钟内」这类事件后限时同样要实时接入
    realtime = card.frequency in ("实时", "分钟级", "事件后限时") or bool(re.search(r"实时|盘中|下单前", text))
    di_raw = ds + (1 if external else 0) + (1 if realtime else 0)
    ui_raw = {"页面": 3, "报表": 2, "提醒": 1, "接口": 1, "数据": 1, "流程": 2}.get(card.req_type, 2) + (1 if "手机端可看" in card.nonfunctional else 0)
    pm_raw = 1 + (2 if "客户" in card.scope_objects else 0) + (1 if re.search(r"权限|只能看|按人|投资经理", text) else 0) + (1 if "对外" in text else 0)
    perf_raw = 4 if realtime else 3 if card.frequency == "分钟级" else 2 if re.search(r"\d+\s*分钟内", text) else 1
    perf_raw += 1 if re.search(r"全品种|所有品种|全部客户|逐客户|五家交易所", text) else 0
    comp_raw = 1 + (2 if "客户" in card.scope_objects else 0) + (1 if re.search(r"合规|适当性|监管|报送", text) else 0) + (1 if re.search(r"留痕|审计", text) else 0) + (1 if re.search(rf"{textutil.ORDER}|指令|阻断", text) else 0)
    dims = {
        "功能点": fp, "数据接入": _clip(di_raw), "界面": _clip(ui_raw),
        "权限": _clip(pm_raw), "性能": _clip(perf_raw), "合规": _clip(comp_raw),
    }
    listed = "、".join(card.features) if card.features else "没有抽出功能点"
    reasons = {
        "功能点": f"{n} 个功能点：{listed}",
        "数据接入": _bounded(di_raw, f"{ds} 类数据源" + ("，含外部 / 非结构化来源" if external else "") + ("，需实时接入" if realtime else "")),
        "界面": _bounded(ui_raw, f"{card.req_type}类" + ("，含手机端" if "手机端可看" in card.nonfunctional else "")),
        "权限": _bounded(pm_raw, ("涉及客户数据，" if "客户" in card.scope_objects else "") + ("需按人 / 产品授权" if pm_raw >= 2 else "沿用现有角色权限")),
        "性能": _bounded(perf_raw, "实时 / 盘中" if realtime else ("有限时要求" if perf_raw >= 2 else "日终批处理")),
        "合规": _bounded(comp_raw, ("客户信息脱敏与审阅；" if "客户" in card.scope_objects else "") + ("触及交易链路；" if re.search(rf"{textutil.ORDER}|指令|阻断", text) else "") + ("留痕 / 审计" if re.search(r"留痕|审计", text) else "常规")),
    }
    return dims, reasons


def _short(src: str) -> str:
    return src.split("（")[0]


def build_tasks(card: Card, text: str) -> list[Task]:
    t = card.req_type
    feats = " ".join(card.features)
    ts: list[Task] = [
        Task("T1", "需求澄清与口径确认", "需求与设计", HUMAN, 0.08, 0.5, "口径由业务确认；模板假定 AI 可协助整理待确认清单，实际节省未校准"),
        Task("T2", "方案与架构设计（含 IT 决策）", "需求与设计", AI_REVIEW, 0.07, 0.6, "需知出决策清单与 ADR 草稿，架构师审定"),
    ]
    for i, src in enumerate(card.data_sources[:4], 1):
        external = bool(re.search(r"现货|公告|第三方|客户", src))
        ts.append(Task(f"D{i}", f"数据接入 · {_short(src)}", "开发", HUMAN if external else AI_REVIEW, 0.07, 0.8 if external else 0.65,
                       "外部 / 非结构化来源要人对接与核对" if external else "内部数据源，AI 生成接入代码，人审口径"))
    if card.indicators:
        ts.append(Task("C1", f"指标计算与 SQL 视图（{'、'.join(card.indicators[:3])}）", "开发", AI_DO, 0.12, 0.45, "公式来自口径本，AI 生成 SQL / 计算任务，人抽样核对数字"))
    if t == "报表":
        ts.append(Task("B1", "报表模板与定时任务", "开发", AI_DO, 0.15, 0.4, "报表平台上的模板与调度，AI 生成配置与样式"))
    elif t == "页面":
        ts += [Task("B1", "前端页面", "开发", AI_DO, 0.14, 0.5, "标准查询 / 看板页面，AI 生成组件，人调交互"),
               Task("B2", "后端接口与服务", "开发", AI_REVIEW, 0.10, 0.6, "AI 生成 CRUD 与查询接口，人审性能与权限")]
    elif t == "提醒":
        ts += [Task("B1", "检测规则引擎与阈值配置", "开发", AI_REVIEW, 0.12, 0.6, "规则由业务定义，AI 实现，人审误报策略与交易日历边界"),
               Task("B2", "推送接入（消息推送中心）", "开发", AI_REVIEW, 0.06, 0.7, "平台接入代码 AI 生成，频控与夜盘策略人定")]
    elif t == "接口":
        ts.append(Task("B1", "查询服务与接口", "开发", AI_REVIEW, 0.15, 0.6, "AI 生成服务骨架与文档，人审鉴权限流"))
    elif t == "数据":
        ts.append(Task("B1", "采集与解析（结构化）", "开发", AI_REVIEW, 0.15, 0.6, "非结构化解析可用大模型，但涉及资金数字须人工复核"))
    else:
        ts.append(Task("B1", "流程建模与审批配置", "开发", HUMAN, 0.15, 0.8, "流程与审批环节是组织决定，AI 只能出草稿"))
    if t in ("页面", "报表") or "客户" in card.scope_objects:
        ts.append(Task("P1", "权限接入（权限中心）", "开发", HUMAN, 0.05, 0.85, "数据权限模型要人定并复核，AI 只省接入样板代码"))
    if "手机端可看" in card.nonfunctional:
        ts.append(Task("M1", "手机端 H5 与移动门户嵌入", "开发", AI_DO, 0.06, 0.5, "H5 适配 AI 生成，SSO 接入按现成模板"))
    if "客户" in card.scope_objects:
        ts.append(Task("S1", "客户版脱敏与合规审阅接入", "开发", AI_REVIEW, 0.06, 0.75, "脱敏规则 AI 起草，合规审阅是红线，必须人签"))
    if re.search(rf"{textutil.ORDER}|指令|限仓|阻断|拒单", text):
        ts.append(Task("R1", "交易链路 / 风控口径核对", "开发", AI_REVIEW, 0.08, 0.85, "三套限仓口径与事前风控边界，AI 写核对逻辑，人逐条验证"))
    if re.search(r"通知文案|解释|文案", text + feats):
        ts.append(Task("W1", "通知文案 / 解释生成", "开发", AI_DO, 0.04, 0.4, "大模型生成，合规措辞模板约束，发送前人审"))
    ts += [
        Task("Q1", "测试用例与测试数据", "测试与比对", AI_DO, 0.08, 0.45, "AI 从验收标准生成用例与期货特有边界数据（夜盘、换月、涨跌停）"),
        Task("Q2", "联调与新旧比对", "测试与比对", HUMAN, 0.10, 0.9, "跨系统联调与数字比对靠人，AI 只帮写比对脚本"),
        Task("L1", "上线变更与培训文档", "上线与培训", AI_REVIEW, 0.07, 0.6, "变更单、操作手册 AI 起草，变更评审按流程走"),
        Task("L2", "验收与签字", "上线与培训", HUMAN, 0.04, 1.0, "业务验收与签字不可替代"),
    ]
    return ts

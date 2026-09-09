"""双轨估算的任务层：把需求拆成任务，每个任务标"谁做"（AI 能做 / AI 做人审 / 人必须做）和 AI 协同系数。

传统人天 = 六维 + 类比得到的总量按任务占比拆开；AI 协同人天 = 每个任务 × 系数。系数按任务性质而非一刀切：
标准化产出（SQL、模板、CRUD、用例）AI 能做；涉及资金数字、交易链路、对客内容的 AI 做人审；口径确认、权限、联调、验收人必须做。
系数为示例值（可按公司 AI 平台使用情况校准），上线后由台账回写实际值。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .intake import Card

AI_DO, AI_REVIEW, HUMAN = "AI 能做", "AI 做人审", "人必须做"


@dataclass
class Task:
    id: str
    name: str
    phase: str          # 需求与设计 / 开发 / 测试与比对 / 上线与培训
    who: str            # AI 能做 / AI 做人审 / 人必须做
    share: float        # 传统人天占比（归一化前）
    coef: float         # AI 协同系数：AI 协同人天 = 传统人天 × coef
    note: str
    trad_days: float = 0.0
    ai_days: float = 0.0


def _short(src: str) -> str:
    return src.split("（")[0]


def build_tasks(card: Card, text: str) -> list[Task]:
    t = card.req_type
    feats = " ".join(card.features)
    ts: list[Task] = [
        Task("T1", "需求澄清与口径确认", "需求与设计", HUMAN, 0.08, 0.5, "口径要业务拍板；需知把待确认清单一次问全，轮次从 3～4 轮降到 1 轮"),
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
    if re.search(r"下单|报单|指令|限仓|阻断|拒单", text):
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


def allocate(tasks: list[Task], total_days: float) -> list[Task]:
    s = sum(t.share for t in tasks) or 1.0
    for t in tasks:
        t.trad_days = round(total_days * t.share / s, 1)
        t.ai_days = round(t.trad_days * t.coef, 1)
    return tasks


def who_split(tasks: list[Task]) -> dict[str, float]:
    total = sum(t.trad_days for t in tasks) or 1.0
    out = {AI_DO: 0.0, AI_REVIEW: 0.0, HUMAN: 0.0}
    for t in tasks:
        out[t.who] += t.trad_days
    return {k: round(v / total, 2) for k, v in out.items()}


def delivery_mode(card: Card, split: dict[str, float], text: str) -> str:
    risky = "客户" in card.scope_objects or bool(re.search(r"下单|指令|限仓|公告", text))
    if split[AI_DO] >= 0.35 and split[HUMAN] <= 0.35 and card.req_type in ("报表", "页面") and not risky:
        return "业务可在公司 AI 平台自建（Vibe Coding），技术部只审口径、权限与上线——适合「人人都有智能体」的自助路径"
    if split[HUMAN] >= 0.45:
        return "技术部主做；AI 协同只省样板代码，重点工时在对接、口径与验证"
    return "技术部主做，AI 协同：AI 出初稿、人审关键环节（资金数字、对客内容、交易链路）"

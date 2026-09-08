"""定架：技术栈 / 架构建议 + IT 决策清单（选项 / 推荐 / 理由 / 影响，生成 ADR）+ 复用发现（对照公司系统目录）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .. import knowledge
from .intake import Card


@dataclass
class Reuse:
    system: str
    owner: str
    matched: list[str]
    integration: str
    maturity: str

    @property
    def score(self) -> int:
        return len(self.matched)


@dataclass
class Decision:
    id: str
    topic: str
    question: str
    options: list[tuple[str, str, str]]     # (方案, 优点, 代价)
    recommended: str
    reason: str
    impact: str


@dataclass
class Architecture:
    stack: list[tuple[str, str]]            # (层, 建议)
    layers: dict[str, list[str]]            # 画图用：入口 / 应用 / 服务 / 数据
    reuse: list[Reuse]
    coverage_line: str
    decisions: list[Decision]
    adr_md: str = ""
    engine: str = "规则"


AFFINITY = {"报表": "报表平台", "提醒": "消息推送中心", "流程": "OA 移动门户", "接口": "交易系统"}   # 需求类型 → 天然承载平台


def discover_reuse(card: Card, text: str) -> list[Reuse]:
    hay = text + " " + " ".join(card.features) + " " + " ".join(card.data_sources) + " " + " ".join(card.channels) + " " + card.req_type
    out = []
    host = AFFINITY.get(card.req_type)
    if card.req_type == "数据" or "公告" in hay:
        host = "公告采集服务" if "公告" in hay else "数据仓库"
    if re.search(r"下单|报单|指令|拒单", hay) and "公告" not in hay:
        host = "交易系统"
    for s in knowledge.systems():
        matched = [c for c in s.capabilities if c in hay]
        if s.name == host:
            matched = [f"承载{card.req_type}类需求"] + matched
        if matched:
            out.append(Reuse(s.name, s.owner, matched, s.integration, s.maturity))
    out.sort(key=lambda r: (-(r.system == host), -r.score))
    return out


def _coverage_line(card: Card, reuse: list[Reuse]) -> str:
    if not reuse:
        return "目录中无可复用系统，需新建。"
    top = reuse[0]
    others = "、".join(r.system for r in reuse[1:4])
    verb = "可作为承载平台" if top.score >= 3 else "可部分复用"
    line = f"「{top.system}」{verb}（命中：{'、'.join(top.matched)}）"
    if others:
        line += f"；数据与渠道复用 {others}"
    line += "。建议在现有系统上加模块而非新建。" if top.score >= 3 else "。核心逻辑需新建服务，外围复用。"
    return line


def suggest_stack(card: Card, text: str, reuse: list[Reuse]) -> tuple[list[tuple[str, str]], dict[str, list[str]]]:
    t = card.req_type
    realtime = card.frequency in ("实时", "分钟级") or bool(re.search(r"实时|盘中|下单前", text))
    stack: list[tuple[str, str]] = []
    if t == "报表":
        stack += [("承载", "报表平台：新建数据集 + 模板，定时任务在结算文件到齐后触发"), ("计算", "SQL 视图 / Python 任务计算新增列与指标，写入报表数据集")]
    elif t == "页面":
        stack += [("前端", "内部 Web（Vue / React）" + ("，H5 嵌入 OA 移动门户" if "手机端可看" in card.nonfunctional else "")),
                  ("后端", "Python / Java 服务 + REST API，权限走权限中心")]
    elif t == "提醒":
        stack += [("检测", "规则引擎 + 定时 / 事件任务（按交易日历）"), ("推送", "消息推送中心：企业微信为主，频控与去重")]
    elif t == "接口":
        stack += [("服务", "旁路查询服务（只读），API 网关鉴权限流")]
    elif t == "数据":
        stack += [("采集", "公告采集服务 / 定时任务拉取源数据"), ("处理", "解析 + 校验 + 落库；非结构化文本可用公司 AI 平台解析，人工复核后生效")]
    else:
        stack += [("流程", "OA 流程引擎建模，表单 + 审批 + 留痕")]
    stack.append(("数据", "行情服务（实时）+ 数据仓库（日终）" if realtime else "数据仓库日终视图（T+1）"))
    if "客户" in card.scope_objects:
        stack.append(("安全", "客户字段脱敏（隐盾规则）；对外内容合规审阅留痕"))
    if re.search(r"公告|通知文案|解释|解析", text):
        stack.append(("AI", "公司 AI 平台（OpenAI 兼容 API）做非结构化解析 / 文案生成，输出需人工复核"))
    layers = {
        "入口": card.channels or ["网页"],
        "应用": [card.title] + [f for f in card.features[:3]],
        "服务": [r.system for r in reuse[:4]] or ["新建服务"],
        "数据": card.data_sources or ["数据仓库"],
    }
    return stack, layers


def build_decisions(card: Card, text: str, reuse: list[Reuse]) -> list[Decision]:
    ds: list[Decision] = []
    top = reuse[0] if reuse else None
    ds.append(Decision("D1", "新建 vs 复用", "在现有系统上加模块，还是新建独立服务？",
                       [(f"复用「{top.system}」加模块" if top else "复用现有平台", "省 30%～50% 工时，权限与运维现成", "受该系统排期与改动窗口约束"),
                        ("新建独立服务", "不受现有系统约束，迭代快", "多一套部署与运维，权限要重接")],
                       (f"复用「{top.system}」" if top and top.score >= 2 else "新建独立服务"),
                       (f"目录命中 {top.score} 项能力，成熟度「{top.maturity}」" if top else "无可复用系统"), "工时 ±30%；影响上线时间"))
    if card.data_sources:
        ds.append(Decision("D2", "数据来源与时点", "数据从哪取、以哪个时点为准？",
                           [("数据仓库日终（T+1）", "稳定、口径统一、成本低", "不能盘中看"), ("行情服务 / 交易系统实时", "盘中可用", "需订阅额度、并发与稳定性要求高"),
                            ("混合：盘中实时 + 日终归档", "两者兼顾", "两套口径要对账")],
                           "行情服务 / 交易系统实时" if re.search(r"实时|盘中|下单前", text) else "数据仓库日终（T+1）",
                           "按原话的时效要求判断" + ("（提到实时 / 盘中）" if re.search(r"实时|盘中|下单前", text) else "（未提实时）"), "架构与成本量级"))
    if card.req_type == "提醒" or re.search(r"提醒|预警|推送|通知", text):
        ds.append(Decision("D3", "批处理 vs 实时检测", "检测按分钟轮询、日终批处理，还是事件驱动？",
                           [("日终批处理", "最简单", "只能次日发现"), ("分钟级轮询", "够用且简单", "延迟几分钟"), ("事件驱动（行情 / 公告推送触发）", "秒级", "接入复杂、误报需治理")],
                           "分钟级轮询" if not re.search(r"公告|秒级", text) else "事件驱动（行情 / 公告推送触发）", "首版求稳，二期升级", "工时 +3～8 人天"))
        ds.append(Decision("D4", "推送渠道", "提醒走什么渠道？", [("企业微信", "触达快、可模板消息", "夜间打扰"), ("邮件", "留痕好", "看得慢"), ("企业微信 + 邮件双发", "兼顾", "重复")],
                           "企业微信" if "企业微信" in card.channels or not card.channels else "、".join(card.channels[:2]), "复用消息推送中心，频控由平台配置", "小"))
    if card.req_type in ("页面", "报表") or "客户" in card.scope_objects:
        ds.append(Decision("D5", "权限模型", "谁能看什么：按角色、按产品、按人？",
                           [("角色 + 产品维度（权限中心现成）", "现成、审计友好", "细粒度需配置"), ("自建权限表", "灵活", "重复建设、审计弱")],
                           "角色 + 产品维度（权限中心现成）", "投资经理只看本人产品是常见诉求，权限中心已支持", "工时 -2 人天"))
    if re.search(r"公告|解析|通知文案|解释|识别", text):
        ds.append(Decision("D6", "是否使用 AI 平台", "非结构化解析 / 文案生成用规则还是大模型？",
                           [("规则 + 模板", "确定性强", "覆盖不全、维护累"), ("公司 AI 平台 + 人工复核", "覆盖广、省人工", "需复核环节与评测"), ("两者结合：规则硬判、模型建议", "兼顾", "多一层逻辑")],
                           "两者结合：规则硬判、模型建议", "涉及资金数字的解析必须有人复核", "合规分 +，工时 +3 人天"))
    if re.search(r"下单|报单|指令|阻断|拒单", text):
        ds.append(Decision("D7", "是否改交易链路", "只做旁路提示，还是改交易系统做阻断？",
                           [("旁路提示（只读）", "不动交易链路、审批简单、可快速上线", "不能强制拦截"), ("交易系统事前风控增强（阻断）", "强约束", "变更审批、灰度、回退要求高，周期长")],
                           "旁路提示（只读）", "首版先解释与测算，阻断纳入二期与交易系统排期", "工时相差 2～3 倍"))
    if "客户" in card.scope_objects or "对外" in text:
        ds.append(Decision("D8", "对外内容与部署", "客户可见内容如何管控？", [("内网生成、人工发送", "最安全", "多一步人工"), ("系统直发客户", "省事", "需合规审阅自动化与更严的脱敏")],
                           "内网生成、人工发送", "首版先保证脱敏与审阅，直发放二期", "合规风险"))
    return ds


def adr_markdown(card: Card, decisions: list[Decision]) -> str:
    today = date.today().isoformat()
    md = [f"# ADR · {card.title}", f"日期：{today}　状态：待评审　生成：需知", ""]
    for d in decisions:
        md += [f"## {d.id} {d.topic}", f"**问题**：{d.question}", "", "**选项**："]
        md += [f"- {o[0]}：优点 —— {o[1]}；代价 —— {o[2]}" for o in d.options]
        md += ["", f"**建议**：{d.recommended}　**理由**：{d.reason}　**影响**：{d.impact}", ""]
    return "\n".join(md)


def build_architecture(card: Card, text: str) -> Architecture:
    reuse = discover_reuse(card, text)
    stack, layers = suggest_stack(card, text, reuse)
    decisions = build_decisions(card, text, reuse)
    arch = Architecture(stack, layers, reuse, _coverage_line(card, reuse), decisions)
    arch.adr_md = adr_markdown(card, decisions)
    return arch

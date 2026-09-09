"""写单：双版需求单——业务版一页纸（业务能签字）+ 技术版（用户故事 / 验收标准 / 数据字典 / 接口草案 / 非功能）。
每条技术条目回链到业务原话。规则模板生成，LLM（若有）润色文字。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..llm import LLM, load_prompt
from .intake import Card, Question

FIELD_DICT: dict[str, tuple[str, str]] = {   # 指标/对象 → (字段名, 类型/口径)
    "单位净值": ("unit_nav", "decimal(10,4)，估值核算 T+1"), "累计净值": ("cum_nav", "decimal(10,4)"), "净值": ("unit_nav", "decimal(10,4)，估值核算 T+1"),
    "回撤": ("max_drawdown", "decimal(8,4)，风控系统口径"), "集中度": ("concentration", "decimal(8,4)，单品种占比"),
    "杠杆": ("leverage", "decimal(8,4)"), "保证金占用": ("margin_used", "decimal(18,2)，公司实收比例"), "保证金": ("margin", "decimal(18,2)"),
    "风险度": ("risk_ratio", "decimal(8,4)，结算系统可用资金口径"), "可用资金": ("available_cash", "decimal(18,2)"),
    "基差": ("basis", "decimal(12,2)，现货 - 期货主力"), "价差": ("spread", "decimal(12,2)"), "涨跌停": ("limit_pct", "decimal(6,4)"),
    "涨跌幅": ("chg_pct", "decimal(8,4)，对上一交易日结算价"), "限仓": ("position_limit", "int，取三套口径最严"),
    "持仓量": ("open_interest", "int，手"), "成交量": ("volume", "int，手"), "追保": ("margin_call", "decimal(18,2)"),
    "资金缺口": ("cash_gap", "decimal(18,2)"), "手续费": ("fee", "decimal(12,2)"),
}
BASE_FIELDS = [("trade_date", "date，交易日"), ("as_of_time", "datetime，数据时点")]
OBJECT_FIELDS = {"资管产品": ("product_code", "varchar，产品代码"), "客户": ("client_id", "varchar，脱敏后客户标识"),
                 "合约": ("contract", "varchar，合约代码，如 RB2601"), "投资经理（按人）": ("manager_id", "varchar"), "交易所": ("exchange", "varchar")}


@dataclass
class Story:
    id: str
    role: str
    want: str
    benefit: str
    origin: str          # 回链原话
    acceptance: list[str] = field(default_factory=list)


@dataclass
class Spec:
    business_md: str
    tech_md: str
    stories: list[Story]
    fields: list[tuple[str, str, str]]     # (中文, 字段, 类型口径)
    api: list[str]
    engine: str = "规则"


def _acceptance(card: Card, feat: str) -> list[str]:
    t = card.req_type
    base = []
    if t == "报表":
        base = ["Given 结算数据已到齐，When 定时任务触发，Then 报表在约定时点前生成且与旧版并行比对一致",
                "Given 样例交易日，When 打开报表，Then 新增列 / 指标数值与业务提供的期望结果一致"]
    elif t == "页面":
        base = ["Given 用户具备权限，When 打开页面并选择筛选条件，Then 3 秒内展示数据且口径与数据源一致",
                "Given 无权限用户，When 访问页面，Then 拒绝访问并记录"]
    elif t == "提醒":
        base = ["Given 触发条件满足，When 检测任务运行，Then 在约定时限内通过指定渠道发出提醒且不重复",
                "Given 非交易时段或非交易日，When 检测任务运行，Then 不发提醒"]
    elif t == "接口":
        base = ["Given 合法调用方，When 请求接口，Then 返回约定结构且 P95 响应 < 500ms", "Given 非法调用，When 请求接口，Then 返回 401 并留痕"]
    elif t == "数据":
        base = ["Given 源数据到达，When 处理任务运行，Then 结果落库且校验通过、异常有告警", "Given 源数据缺失，When 任务运行，Then 标注缺失并通知负责人"]
    else:
        base = ["Given 流程发起，When 各环节审批，Then 状态流转正确且全程留痕"]
    if re.search(r"提醒|预警|通知", feat) and t != "提醒":
        base.append("Given 异常条件满足，When 检测运行，Then 在约定时限内推送提醒")
    if re.search(r"客户|对外", feat):
        base.append("Given 客户版输出，When 生成，Then 不含内部字段且通过合规审阅留痕")
    return base[:3]


def build_stories(card: Card) -> list[Story]:
    role = card.users[0] if card.users else "使用者"
    stories = []
    for i, (f, raw) in enumerate(zip(card.features, card.raw_features or card.features), 1):
        stories.append(Story(f"US-{i:02d}", role, f, "减少人工整理与漏看，按时拿到可用结果", raw, _acceptance(card, f)))
    return stories


def build_fields(card: Card) -> list[tuple[str, str, str]]:
    out = [(cn, *BASE_FIELDS[i]) for i, cn in enumerate(("交易日", "数据时点"))]
    for obj in card.scope_objects:
        if obj in OBJECT_FIELDS:
            out.append((obj, *OBJECT_FIELDS[obj]))
    if card.symbols:
        out.append(("品种", "symbol", "varchar，品种代码"))
    for ind in card.indicators:
        if ind in FIELD_DICT:
            out.append((ind, *FIELD_DICT[ind]))
    if "较上一交易日变动" in " ".join(card.features) or re.search(r"变动|较上一", " ".join(card.features)):
        out.append(("较上一交易日变动", "chg_vs_prev", "decimal(10,4)，基准=上一交易日结算数据"))
    return out


def build_api(card: Card) -> list[str]:
    slug = {"报表": "reports", "页面": "views", "提醒": "alerts", "接口": "api", "数据": "datasets", "流程": "workflows"}.get(card.req_type, "items")
    obj = "product" if "资管产品" in card.scope_objects else ("symbol" if card.symbols else "item")
    api = [f"GET /api/v1/{slug}/{obj}s?trade_date=YYYY-MM-DD&{obj}=… → 列表（分页、按权限过滤）"]
    if card.req_type in ("报表",):
        api.append(f"POST /api/v1/{slug}/export → 导出 Excel / PDF（留痕、水印）")
    if card.req_type == "提醒" or "提醒" in " ".join(card.features):
        api.append("POST /api/v1/alerts/rules → 阈值与渠道配置；GET /api/v1/alerts/history → 提醒记录")
    if "客户" in card.scope_objects:
        api.append(f"GET /api/v1/{slug}/client-view → 客户版（脱敏字段集）")
    return api


def _q_by_cat(questions: list[Question], cats: tuple[str, ...]) -> list[Question]:
    return [q for q in questions if q.category in cats]


def build_spec(card: Card, questions: list[Question], reuse_line: str = "", llm: LLM | None = None) -> Spec:
    stories = build_stories(card)
    fields = build_fields(card)
    api = build_api(card)
    answered = [q for q in questions if q.answer.strip()]
    pending_high = [q for q in questions if q.impact == "高" and not q.answer.strip()]

    # ---- 业务版（一页纸）----
    b = [f"# {card.title}", "", f"**提出方**：{card.requester or '未注明'}　**来源**：{card.source}　**类型**：{card.req_type}"
         + (f"（兼 {'、'.join(card.secondary_types)}）" if card.secondary_types else ""), "",
         "## 1. 为什么做", f"{card.goal}。", "",
         "## 2. 谁用、什么时候用", f"使用者：{'、'.join(card.users)}；触发：{card.trigger}；频率：{card.frequency}"
         + (f"；期望上线：{card.deadline}" if card.deadline else "；上线时间：待定"), "",
         "## 3. 要做什么"]
    b += [f"{i}. {f}" for i, f in enumerate(card.features, 1)]
    b += ["", "## 4. 数据与口径"]
    b += [f"- 数据来源：{'、'.join(card.data_sources) or '待确认'}"]
    for q in _q_by_cat(questions, ("口径", "时点与日历", "数据来源", "风控口径", "品种范围")):
        mark = "✅" if q.answer.strip() else "◻︎默认"
        b.append(f"- {mark} {q.question.split('？')[0]}？→ {q.resolved}")
    b += ["", "## 5. 不做什么（本期范围外）"]
    scope_q = [q for q in questions if q.category == "范围"]
    b += [f"- {scope_q[0].resolved}" if scope_q else "- 只做上述功能点，其余列入二期"]
    b += ["", "## 6. 输出形态与非功能", f"- 渠道 / 形态：{'、'.join(card.channels) or '网页 + Excel（默认）'}"]
    b += [f"- {n}" for n in card.nonfunctional]
    b += ["", "## 7. 验收", "- 业务提供 3 个交易日样例与期望结果；上线前新旧并行比对；由提出方主管签字。"]
    if reuse_line:
        b += ["", "## 8. 技术部初判", f"- {reuse_line}"]
    b += ["", "## 9. 仍待业务确认（高影响）"] + ([f"- {q.question}" for q in pending_high] or ["- 无"])
    b += ["", f"> 需知 自动生成 · 引擎：{card.engine} · 已答复 {len(answered)}/{len(questions)} 项，其余按默认假设"]

    # ---- 技术版 ----
    t = [f"# {card.title} · 技术需求", "", "## A. 用户故事与验收标准"]
    for s in stories:
        t += [f"**{s.id}** 作为 *{s.role}*，我希望 **{s.want}**，以便 {s.benefit}。", f"> 原话：{s.origin}"]
        t += [f"- {a}" for a in s.acceptance] + [""]
    t += ["## B. 数据字典（草案）", "| 中文 | 字段 | 类型 / 口径 |", "|---|---|---|"]
    t += [f"| {cn} | `{f}` | {tp} |" for cn, f, tp in fields]
    t += ["", "## C. 接口草案"] + [f"- `{a}`" for a in api]
    t += ["", "## D. 非功能需求"]
    t += [f"- {n}" for n in card.nonfunctional] or ["- 无特别要求（默认：权限中心鉴权、操作留痕）"]
    perf = [q for q in questions if q.category == "性能与时效"]
    t += [f"- 时效：{perf[0].resolved}" if perf else f"- 时效：{card.frequency}"]
    t += ["- 安全：进模型 / 外发前脱敏；导出留痕加水印"]
    t += ["", "## E. 依赖与假设"] + [f"- {a}" for a in card.assumptions]
    t += [f"- 默认假设（未答复项）：{q.question.split('？')[0]} → {q.default}" for q in questions if not q.answer.strip() and q.impact == "高"]
    t += ["", "## F. 范围外 / 二期"] + [f"- {scope_q[0].resolved}" if scope_q else "- 二期：对账（需求变更影响）、更多渠道"]
    spec = Spec("\n".join(b), "\n".join(t), stories, fields, api)

    if llm is not None and llm.mode == "api":
        spec = _polish(spec, card, llm)
    return spec


def _polish(spec: Spec, card: Card, llm: LLM) -> Spec:
    system = load_prompt("spec_polish") or "你是期货公司技术部的需求分析师，把需求单润色得简洁、专业、可签字。保持 Markdown 结构与所有条目，只改措辞。只输出 Markdown。"
    try:
        out = llm.chat(system, spec.business_md, temperature=0.2)
        if out and _polish_ok(spec.business_md, out):
            spec.business_md = out
            spec.engine = "规则 + 模型"
    except Exception:
        pass
    return spec


def _polish_ok(orig: str, out: str) -> bool:
    """润色不能丢内容：章节数一致、默认/已答复标记不少、篇幅在 0.6～1.6 倍之间。"""
    h = lambda t: sum(1 for ln in t.splitlines() if ln.startswith("## "))
    marks = lambda t: t.count("◻︎默认") + t.count("✅")
    return h(out) == h(orig) and marks(out) >= marks(orig) and 0.6 * len(orig) <= len(out) <= 1.6 * len(orig)

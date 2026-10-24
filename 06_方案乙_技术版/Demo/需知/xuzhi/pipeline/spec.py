"""写单：双版需求单——业务版一页纸（业务能签字）+ 技术版（用户故事 / 验收标准 / 数据字典 / 接口草案 / 非功能）。
每条技术条目回链到业务原话。规则模板生成，LLM（若有）润色文字。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..llm import LLM, load_prompt
from ..privacy import Redactor, label_counts
from ..textutil import CLIENT_FACING, find_added_columns, markdown_literal, merge_label, same_label
from .intake import Card, Question, card_is_futures

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
GENERIC_BASE_FIELDS = [("日期", "biz_date", "date，日期"), ("数据时点", "as_of_time", "datetime，数据时点")]   # 非期货需求不写交易日
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


def _acceptance(card: Card, feat: str, futures: bool = True) -> list[str]:
    t = card.req_type
    sample = "样例交易日" if futures else "样例数据"
    base = []
    if t == "报表":
        base = [f"Given {'结算数据已到齐' if futures else '样例数据已准备'}，When 定时任务触发，Then 报表在约定时点前生成且与旧版并行比对一致",
                f"Given {sample}，When 打开报表，Then 新增列 / 指标数值与业务提供的期望结果一致"]
    elif t == "页面":
        base = ["Given 用户具备权限，When 打开页面并选择筛选条件，Then 3 秒内展示数据且口径与数据源一致",
                "Given 无权限用户，When 访问页面，Then 拒绝访问并记录"]
    elif t == "提醒":
        base = ["Given 触发条件满足，When 检测任务运行，Then 在约定时限内通过指定渠道发出提醒且不重复",
                f"Given {'非交易时段或非交易日' if futures else '非工作日'}，When 检测任务运行，Then 不发提醒"]
    elif t == "接口":
        base = ["Given 合法调用方，When 请求接口，Then 返回约定结构且 P95 响应 < 500ms", "Given 非法调用，When 请求接口，Then 返回 401 并留痕"]
    elif t == "数据":
        base = ["Given 源数据到达，When 处理任务运行，Then 结果落库且校验通过、异常有告警", "Given 源数据缺失，When 任务运行，Then 标注缺失并通知负责人"]
    else:
        base = ["Given 流程发起，When 各环节审批，Then 状态流转正确且全程留痕"]
    # 本条功能点自己的验收放最前，不被类型模板挤掉
    where = "报表" if t == "报表" else "页面"
    own = [f"Given {sample}，When 打开{where}，Then {where}含「{col}」列，按展示格式口径显示" for col in find_added_columns(feat)]
    if re.search(r"限仓|超限|可下|能下多少", feat):   # 「还能下多少手」说的也是还可下
        own.append("Given 持仓 N、限仓 M，When 拟下 K 手且 N+K>M，Then 下单前提示超限并显示还可下 M−N 手")
    if re.search(r"提醒|预警|通知", feat) and t != "提醒":
        base.append("Given 异常条件满足，When 检测运行，Then 在约定时限内推送提醒")
    if CLIENT_FACING.search(feat):   # 只是提到客户不算对客，不加客户版验收
        base.append("Given 客户版输出，When 生成，Then 不含内部字段且通过合规审阅留痕")
    return (own + base)[:3]


def _want(feature: str) -> str:
    """用户故事写成陈述：去掉「能不能」「可不可以」和句尾的「吗 / 呢」（功能点原文不动）。"""
    s = re.sub(r"能不能|可不可以|能否|可否", "", feature or "")
    s = re.sub(r"[吗呢]\s*[？?]?$", "", s.strip())
    return s.strip("，, ") or feature


ORIGIN_NONE = "（原话里没有对应的一句：模型补充的功能点，请业务确认）"


def _likeness(a: str, b: str) -> float:
    """两句话的字面相似度（相邻两字的重合，Dice）。"""
    ga, gb = ({t[i:i + 2] for i in range(len(t) - 1)} for t in (re.sub(r"\W", "", a or ""), re.sub(r"\W", "", b or "")))
    return 2 * len(ga & gb) / (len(ga) + len(gb)) if ga and gb else 0.0


def _origins(card: Card) -> list[str]:
    """每条功能点回链原句。规则抽的功能点与原句同序；模型润色后可能换序、合并或补充，
    同位原句不像时按字面相似度改配最像的一句，都不像就标明是模型补充，不硬配。"""
    raws = list(card.raw_features or [])
    pool = [r for r in raws if r.strip()]
    if not pool:
        return list(card.features)
    out = []
    for i, f in enumerate(card.features):
        same = raws[i] if i < len(raws) else ""
        best = max(pool, key=lambda r: _likeness(f, r))
        if same.strip() and _likeness(f, same) >= _likeness(f, best) - 0.05:
            out.append(same)
        else:
            out.append(best if _likeness(f, best) >= 0.15 else ORIGIN_NONE)
    return out


def build_stories(card: Card) -> list[Story]:
    role = card.users[0] if card.users else "使用者"
    futures = card_is_futures(card)
    stories = []
    for i, (f, raw) in enumerate(zip(card.features, _origins(card)), 1):
        stories.append(Story(f"US-{i:02d}", role, _want(f), "减少人工整理与漏看，按时拿到可用结果", raw, _acceptance(card, f, futures)))
    return stories


def _column_field(col: str, n: int, futures: bool) -> tuple[str, str, str]:
    """新增列逐列给字段；变动类的比较基准跟着周期走（周报 → 上周，日报 → 上一交易日）。"""
    if re.search(r"变动|较上|涨跌", col):
        if "周" in col:
            return col, "chg_vs_prev_week", f"decimal(10,4)，基准={'上周最后一个交易日' if futures else '上周最后一天'}"
        if "月" in col:
            return col, "chg_vs_prev_month", f"decimal(10,4)，基准={'上月最后一个交易日' if futures else '上月最后一天'}"
        return col, "chg_vs_prev", f"decimal(10,4)，基准={'上一交易日' if futures else '上一期'}"
    return col, f"new_col_{n}", "类型待定，按展示格式口径"


def build_fields(card: Card) -> list[tuple[str, str, str]]:
    futures = card_is_futures(card)
    out = [(cn, *BASE_FIELDS[i]) for i, cn in enumerate(("交易日", "数据时点"))] if futures else list(GENERIC_BASE_FIELDS)
    for obj in card.scope_objects:
        if obj in OBJECT_FIELDS:
            out.append((obj, *OBJECT_FIELDS[obj]))
    if card.symbols:
        out.append(("品种", "symbol", "varchar，品种代码"))
    for ind in card.indicators:
        if ind in FIELD_DICT:
            out.append((ind, *FIELD_DICT[ind]))
    cols: list[str] = []
    for snip in [*card.features, *card.raw_features]:
        for col in find_added_columns(snip):
            merge_label(cols, col)
    for n, col in enumerate(cols, 1):
        if not any(same_label(col, cn) for cn, _f, _t in out):
            out.append(_column_field(col, n, futures))
    # 点名了变动、却不是「加一列」的说法（「日报里要有较上一交易日变动」）：照样给字段，基准跟着说法走
    if not any(re.search(r"变动|较上", cn) for cn, _f, _t in out):
        m = re.search(r"较上[^\s，,。；;、和与及]{1,8}?(?:变动|涨跌幅?|变化)", " ".join([*card.features, *card.raw_features]))
        if m:
            out.append(_column_field(m.group(0), len(cols) + 1, futures))
    return out


def build_api(card: Card) -> list[str]:
    slug = {"报表": "reports", "页面": "views", "提醒": "alerts", "接口": "api", "数据": "datasets", "流程": "workflows"}.get(card.req_type, "items")
    obj = "product" if "资管产品" in card.scope_objects else ("symbol" if card.symbols else "item")
    day = "trade_date" if card_is_futures(card) else "date"
    api = [f"GET /api/v1/{slug}/{obj}s?{day}=YYYY-MM-DD&{obj}=… → 列表（分页、按权限过滤）"]
    if card.req_type in ("报表",):
        api.append(f"POST /api/v1/{slug}/export → 导出 Excel / PDF（留痕、水印）")
    if card.req_type == "提醒" or "提醒" in " ".join(card.features):
        api.append("POST /api/v1/alerts/rules → 阈值与渠道配置；GET /api/v1/alerts/history → 提醒记录")
    if card.client_facing:   # 原话要给客户看才出客户版接口；只是涉及客户数据不算
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
    b += [f"{i}. {markdown_literal(f)}" for i, f in enumerate(card.features, 1)]
    b += ["", "## 4. 数据与口径"]
    b += [f"- 数据来源：{'、'.join(card.data_sources) or '待确认'}"]
    for q in _q_by_cat(questions, ("口径", "时点与日历", "数据来源", "风控口径", "品种范围", "展示格式")):
        mark = ("🔁沿用" if getattr(q, "recalled", "") else "✅") if q.answer.strip() else "◻︎默认"
        b.append(f"- {mark} {q.question.split('？')[0]}？→ {q.resolved}")
    b += ["", "## 5. 不做什么（本期范围外）"]
    scope_q = [q for q in questions if q.category == "范围"]
    b += [f"- {scope_q[0].resolved}" if scope_q else "- 只做上述功能点；原话未提及的内容本期不做"]
    b += ["", "## 6. 输出形态与非功能", f"- 渠道 / 形态：{'、'.join(card.channels) or '网页 + Excel（默认）'}"]
    b += [f"- {n}" for n in card.nonfunctional]
    samples = "3 个交易日样例" if card_is_futures(card) else "3 个样例日"
    b += ["", "## 7. 验收", f"- 业务提供 {samples}与期望结果；上线前新旧并行比对；由提出方主管签字。"]
    if reuse_line:
        b += ["", "## 8. 技术部初判", f"- {reuse_line}"]
    b += ["", "## 9. 仍待业务确认（高影响）"] + ([f"- {q.question}" for q in pending_high] or ["- 无"])
    recalled_n = sum(1 for q in answered if getattr(q, "recalled", ""))
    b += ["", f"> 需知 自动生成 · 引擎：{card.engine} · 已答复 {len(answered)}/{len(questions)} 项"
          + (f"（其中 {recalled_n} 项沿用该提出方上次口径）" if recalled_n else "") + "，其余按默认假设"]

    # ---- 技术版 ----
    t = [f"# {card.title} · 技术需求", "", "## A. 用户故事与验收标准"]
    for s in stories:
        t += [f"**{s.id}** 作为 *{s.role}*，我希望 **{markdown_literal(s.want)}**，以便 {s.benefit}。", f"> 原话：{markdown_literal(s.origin)}"]
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
    # 没问范围题时不写与本需求无关的二期占位
    t += ["", "## F. 范围外 / 二期"] + [f"- {scope_q[0].resolved}" if scope_q else
                                      "- 原话未提及的范围（历史数据回补、其他产品线、更多推送渠道）本期不做，需要请另提"]
    spec = Spec("\n".join(b), "\n".join(t), stories, fields, api)

    if llm is not None and llm.mode == "api":
        spec = _polish(spec, card, llm)
    return spec


def _polish(spec: Spec, card: Card, llm: LLM) -> Spec:
    system = load_prompt("spec_polish") or "你是期货公司技术部的需求分析师，把需求单润色得简洁、专业、可签字。保持 Markdown 结构与所有条目，只改措辞。只输出 Markdown。"
    try:
        llm.current_purpose = "写单润色"
        # 业务版里有业务答复原文（页面 / 企微直接填写，没经过隐盾）：出站前同样脱敏，回来再还原。
        # 功能点里已有原话的标签（Markdown 转义成 \<手机\_1\>），新标签接着编号，免得撞号还原错位
        red = Redactor().redact(spec.business_md, start=label_counts(spec.business_md))
        out = llm.chat(system, red.text, temperature=0.2)
        if out and _polish_ok(red.text, out):
            spec.business_md = Redactor.restore(out, red.mapping)
            spec.engine = "规则 + 模型"
    except Exception:
        pass
    return spec


def _polish_ok(orig: str, out: str) -> bool:
    """润色不能丢内容：章节数一致、默认/已答复标记不少、篇幅在 0.6～1.6 倍之间。"""
    h = lambda t: sum(1 for ln in t.splitlines() if ln.startswith("## "))
    marks = lambda t: t.count("◻︎默认") + t.count("✅")
    return h(out) == h(orig) and marks(out) >= marks(orig) and 0.6 * len(orig) <= len(out) <= 1.6 * len(orig)

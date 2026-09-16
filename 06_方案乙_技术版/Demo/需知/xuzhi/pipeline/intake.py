"""问清：从一堆话里抽出需求卡片，生成待确认清单与确认消息。规则引擎兜底，LLM（若有）润色补充。"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from .. import knowledge, textutil
from ..llm import LLM, load_prompt

TYPE_RULES: list[tuple[str, str, int]] = [
    (r"日报|周报|月报|报表|报告|一列|加一列|改版|定期", "报表", 2),
    (r"页面|看板|大屏|查询|展示|界面|H5|检索", "页面", 2),
    (r"提醒|预警|告警|通知|推送|提示|别漏", "提醒", 2),
    (r"接口|API|对接|调用", "接口", 2),
    (r"流程|审批|电子化|签字", "流程", 2),
    (r"抓取|采集|落库|测算|计算|识别|解析|接入", "数据", 1),
]
TRIGGER_RULES = [(r"公告|发布后|拒单|下单|报单|指令|异动|变动时|触发", "事件"), (r"每天|每日|日报|周报|每周|定时|日终|结算后", "定时"), (r".*", "手动")]
FREQ_RULES = [(r"实时|盘中|下单前|报单前|秒级", "实时"), (r"分钟", "分钟级"), (r"结算后|日终", "结算后"), (r"每天|每日|日报", "每日"),
              (r"周报|每周|周五", "每周"), (r"公告发布后|30 ?分钟内", "事件后限时"), (r".*", "按需")]
DEADLINE_RE = re.compile(r"(下周|本周|月底|年底|\d+\s*(?:天|日|周|个月|分钟)内|尽快|越快越好|周会|之前)")
NONFUNC_RULES = [(r"手机(?!_\d)|移动|随时|出差", "手机端可看"), (r"留痕|事后查询|审计", "全程留痕可查"), (r"脱敏|信息安全|不该给的|客户版", "客户信息脱敏"),
                 (r"夜间|夜盘", "夜间 / 夜盘时段可用"), (r"\d+\s*分钟内|实时|盘中", "时效要求（实时 / 限时）"), (r"权限|只能看|谁能看", "数据权限控制"),
                 (r"排序|按风险", "结果按风险排序"), (r"导出|下载", "支持导出")]

TITLE_TEMPLATES: list[tuple[str, str]] = [
    (r"净值日报.*对标指数", "净值日报改版：新增对标指数列"),
    (r"净值日报.*(加|新增|放进|改)", "净值日报改版：新增变动列与风险指标"),
    (r"基差.*(监控|页面|看板)", "基差监控页面（含异常提醒）"),
    (r"(下单|报单).*(限仓|超限)|限仓.*(提示|提醒)", "下单前限仓超限提示"),
    (r"(保证金|限仓).*(调整|公告).*(测算|影响)|公告.*(测算|影响)", "交易所保证金 / 限仓调整影响测算与通知"),
    (r"(异动).*(提醒|解释)", "品种异动提醒"),
    (r"(价差|跨期).*(监控|提醒)", "价差监控与提醒"),
]


@dataclass
class Question:
    id: str
    category: str
    tag: str            # 期货 / 通用
    impact: str         # 高 / 中 / 低
    question: str
    why: str
    default: str
    answer: str = ""    # 业务答复（空 = 未答，按默认假设）

    @property
    def resolved(self) -> str:
        return self.answer.strip() or self.default


@dataclass
class Card:
    title: str = ""
    source: str = ""
    requester: str = ""
    req_type: str = ""
    secondary_types: list[str] = field(default_factory=list)
    goal: str = ""
    users: list[str] = field(default_factory=list)
    trigger: str = ""
    frequency: str = ""
    symbols: list[str] = field(default_factory=list)
    indicators: list[str] = field(default_factory=list)
    scope_objects: list[str] = field(default_factory=list)   # 产品 / 客户 / 合约 等对象
    data_sources: list[str] = field(default_factory=list)
    channels: list[str] = field(default_factory=list)
    deadline: str = ""
    nonfunctional: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)        # 功能点（清洗后的原话）
    raw_features: list[str] = field(default_factory=list)    # 对应原句（回链）
    assumptions: list[str] = field(default_factory=list)
    engine: str = "规则"
    title_from_template: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    def keywords(self) -> set[str]:
        ks = set(self.symbols) | set(self.indicators) | set(self.scope_objects) | {self.req_type}
        for f in self.features:
            ks.update(t for t in textutil.INDICATORS if t in f)
        return ks


def _classify(text: str) -> tuple[str, list[str]]:
    scores: dict[str, int] = {}
    for pat, label, w in TYPE_RULES:
        n = len(re.findall(pat, text))
        if n:
            scores[label] = scores.get(label, 0) + n * w
    for pat, label in ((r"做(一)?个[^，。]{0,12}(页面|看板)|监控页面|查询页面", "页面"), (r"做(一)?个[^，。]{0,12}(报表|日报|周报)", "报表"),
                       (r"做(一)?个[^，。]{0,12}接口", "接口"), (r"自动(抓取|采集|测算|计算)", "数据")):
        if re.search(pat, text):
            scores[label] = scores.get(label, 0) + 4
    if not scores:
        return "页面", []
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    primary = ranked[0][0]
    secondary = [k for k, v in ranked[1:] if v >= 2]
    return primary, secondary[:2]


def _first(rules: list[tuple[str, str]], text: str) -> str:
    for pat, label in rules:
        if re.search(pat, text):
            return label
    return ""


def _title(text: str, req_type: str, card: Card) -> str:
    for pat, title in TITLE_TEMPLATES:
        if re.search(pat, text):
            card.title_from_template = True
            return title
    obj = "、".join((card.indicators or card.symbols or card.scope_objects)[:2])
    noun = {"报表": "报表", "页面": "页面", "提醒": "提醒", "接口": "接口", "流程": "流程", "数据": "数据处理"}.get(req_type, "功能")
    return f"{obj}{noun}" if obj else (card.features[0][:24] if card.features else f"新{noun}需求")


def _scope_objects(text: str) -> list[str]:
    out = []
    for pat, label in ((r"产品", "资管产品"), (r"客户|投资者", "客户"), (r"合约", "合约"), (r"投资经理", "投资经理（按人）"), (r"交易所", "交易所"),
                       (r"账户|资金账号", "账户")):
        if re.search(pat, text) and label not in out:
            out.append(label)
    return out


def _assumptions(card: Card, text: str = "") -> list[str]:
    a = []
    futures = looks_futures_domain(text, card.title, " ".join(card.features), " ".join(card.indicators))
    if not card.deadline:
        a.append("原话未提上线时间 → 按正常排期")
    if card.req_type in ("报表", "页面") and not card.channels:
        a.append("未提输出形态 → 默认网页" + (" + Excel" if futures else ""))
    if card.req_type == "提醒" and "企业微信" not in card.channels:
        a.append("未指定提醒渠道 → 默认企业微信" if futures else "未指定提醒渠道 → 待业务确认渠道")
    if futures and "客户" in card.scope_objects:
        a.append("涉及客户 → 默认需脱敏与合规审阅")
    if card.frequency == "按需":
        a.append("未提频率 → 默认每日一次（结算后）" if futures else "未提频率 → 按业务约定")
    return a


def _neutral_goal(card: Card) -> str:
    who = "、".join(card.users[:2]) or "相关同事"
    focus = "、".join((card.indicators or card.symbols or card.scope_objects)[:3])
    if card.features:
        return f"落实「{card.title or card.features[0]}」，让{who}能完成：{card.features[0]}"
    if focus:
        return f"落实「{card.title}」，让{who}能用上与{focus}相关的{card.req_type or '功能'}"
    return f"落实「{card.title or '该需求'}」，让{who}能用上所需能力"


def _futures_goal(card: Card) -> str:
    return (
        f"让{'、'.join(card.users[:2])}{'定时' if card.trigger == '定时' else '及时'}拿到"
        f"{'、'.join((card.indicators or card.symbols)[:3]) or '所需'}相关的{card.req_type}结果，减少人工整理与漏看"
    )


def extract_card(text: str, source_hint: str = "") -> Card:
    body = textutil.strip_meta(text)
    card = Card(source=textutil.detect_source(text, source_hint))
    hits = [(text.find(k), v) for k, v in textutil.DEPARTMENTS if k in text]
    if hits:
        card.requester = min(hits)[1]
    card.req_type, card.secondary_types = _classify(body)
    card.trigger = _first(TRIGGER_RULES, body)
    card.frequency = _first(FREQ_RULES, body)
    card.symbols = textutil.find_symbols(body)
    card.indicators = textutil.find_indicators(body)
    for col in textutil.find_added_columns(body):
        textutil.merge_label(card.indicators, col)
    card.scope_objects = _scope_objects(body)
    card.data_sources = textutil.match_rules(body, textutil.DATA_SOURCE_RULES)
    card.channels = textutil.match_rules(body, textutil.CHANNEL_RULES)
    m = DEADLINE_RE.search(body)
    card.deadline = m.group(1) if m else ""
    card.nonfunctional = textutil.match_rules(body, NONFUNC_RULES)
    own = card.requester.split("·")[-1] if card.requester else ""
    roles = [r for r in textutil.USER_ROLES if r in text and r != "领导" and not (own and r in own and r != own)]
    card.users = ([own] if own and own not in roles else []) + roles or ["提出人"]
    for s in textutil.sentences(body):
        if textutil.FEATURE_CUES.search(s) and not textutil.BACKGROUND.search(s) and not re.match(r"^(领导|王总|谢谢|技术部同事|小李|在吗)", s):
            f = textutil.clean_feature(s)
            if 4 <= len(f) <= 80 and f not in card.features:
                card.features.append(f)
                card.raw_features.append(s)
    card.features, card.raw_features = card.features[:12], card.raw_features[:12]
    card.title = _title(body, card.req_type, card)
    futures = looks_futures_domain(body, card.title, " ".join(card.features), " ".join(card.indicators))
    card.goal = _futures_goal(card) if futures else _neutral_goal(card)
    card.assumptions = _assumptions(card, body)
    return card


_FUTURES_HINT = re.compile(
    r"期货|期权|合约|主力|基差|价差|结算|日终|夜盘|限仓|保证金|净值|"
    r"持仓|交割|品种|报单|下单|交易所|对标指数|资管产品|换月"
)


def looks_futures_domain(text: str, *extra: str) -> bool:
    blob = " ".join([text or "", *extra])
    return bool(_FUTURES_HINT.search(blob))


def _added_column_names(text: str, card: Card) -> list[str]:
    """从原话与功能点抽出明确「加一列」的列名。"""
    cols: list[str] = []
    for snip in [text or "", card.title or ""] + list(card.features or []) + list(card.raw_features or []):
        for col in textutil.find_added_columns(snip):
            textutil.merge_label(cols, col)
    return cols[:6]


def _column_display_questions(
    text: str,
    card: Card,
    *,
    table_headers: list[str] | None = None,
) -> list[Question]:
    """往表里加列时追问展示格式等（与业务域无关）。"""
    cols = _added_column_names(text, card)
    if not cols:
        return []
    label = "、".join(f"「{c}」" for c in cols)
    headers = [h for h in (table_headers or []) if h and len(h) >= 1][:12]
    header_hint = "、".join(headers) if headers else ""

    qs: list[Question] = [
        Question(
            "C_FMT",
            "展示格式",
            "通用",
            "高",
            (
                f"新增列{label}在表里按什么格式显示：文本、整数、小数、百分数、日期时间，还是其它？"
                f"若是数字，保留几位小数、要不要百分号、要不要千分位、单位写在表头还是单元格里？"
            ),
            "格式不定会导致前后端、导出与出样对不齐，上线后再改样式成本高。",
            "与相邻数字列对齐；说不清则小数 2 位、非百分数、不加千分位，单位放表头括号内。",
        ),
        Question(
            "C_NULL",
            "展示格式",
            "通用",
            "中",
            f"新增列{label}在无数据或算不出时怎么显示：空白、「—」、0，还是其它占位？",
            "空值展示不一致，业务会当成 0 或漏数。",
            "无数据显示「—」，与表内其它空单元格一致。",
        ),
        Question(
            "C_POS",
            "界面",
            "通用",
            "中",
            (
                f"新增列{label}插在哪一列旁边？"
                + (f"（现有表头：{header_hint}）" if header_hint else "（相对现有哪一列表头）")
                + "是否需要可排序、可筛选？"
            ),
            "列位置和交互不定，界面与导出列序会返工。",
            ("插在表尾；暂不单独做排序/筛选，沿用表原有能力。" if not header_hint else f"插在「{headers[-1]}」右侧；暂不单独做排序/筛选。"),
        ),
        Question(
            "C_SIGN",
            "展示格式",
            "通用",
            "低",
            f"若{label}是数字，正负要不要用颜色区分（如红跌绿涨）？导出 Excel 是否与页面同一套格式？",
            "着色与导出格式常被当成「改了需求」。",
            "页面不强制着色；导出与页面小数位、百分号规则一致。",
        ),
    ]
    return qs


def build_questions(
    text: str,
    card: Card,
    limit: int = 12,
    *,
    has_materials: bool = False,
    table_headers: list[str] | None = None,
) -> list[Question]:
    """规则追问。有 HTML/Git 时不硬套期货模板（除非原话/材料本身像期货业务）。"""
    body = textutil.strip_meta(text)
    futures_ok = looks_futures_domain(body, card.title, " ".join(card.features), " ".join(card.indicators))
    qs: list[Question] = list(_column_display_questions(body, card, table_headers=table_headers))
    seen_ids = {q.id for q in qs}
    seen_cat: dict[str, int] = {}
    for q in qs:
        seen_cat[q.category] = seen_cat.get(q.category, 0) + 1
    for p in sorted(knowledge.probes(), key=lambda p: (knowledge.IMPACT_ORDER[p.impact], p.tag != "期货")):
        if not p.hits(body):
            continue
        if has_materials and p.tag == "期货" and not futures_ok:
            continue
        if not has_materials and p.tag == "期货" and not futures_ok:
            continue
        if p.id == "T29" and not card.deadline:
            continue
        if p.id in seen_ids:
            continue
        if seen_cat.get(p.category, 0) >= 3:
            continue
        seen_cat[p.category] = seen_cat.get(p.category, 0) + 1
        qs.append(Question(p.id, p.category, p.tag, p.impact, p.question, p.why, p.default))
        seen_ids.add(p.id)
    # 加列场景优先保留展示格式题，再截断
    return qs[:limit]


def _salutation(card: Card) -> str:
    """发给业务的开头称呼：有提出方用「××你好」，没有则用「您好」，避免「您你好」。"""
    who = (card.requester.split("·")[0] if card.requester else "").strip()
    if not who or who in ("您", "你"):
        return "您好"
    if who.endswith(("您好", "你好")):
        return who
    return f"{who}你好"


_FUTURES_Q_HINT = re.compile(
    r"结算|夜盘|主力合约|限仓|交割|保证金|对标指数|交易日|收盘价|持仓量|期货"
)


def confirm_message(card: Card, questions: list[Question], text: str = "") -> str:
    """发给业务的确认消息：问题与理解均跟随原话领域，不硬套期货口径。"""
    futures = looks_futures_domain(
        text, card.title, card.goal, " ".join(card.features), " ".join(q.question for q in questions)
    )
    usable = []
    for q in questions:
        if not futures and (q.tag == "期货" or _FUTURES_Q_HINT.search(q.question) or _FUTURES_Q_HINT.search(q.default)):
            continue
        usable.append(q)
    highs = [q for q in usable if q.impact == "高" and not q.answer.strip()]
    mids = [q for q in usable if q.impact != "高" and not q.answer.strip()]
    lines = [f"{_salutation(card)}，关于「{card.title}」，开工前想和你确认几件事，确认清楚了我们就能给准确的排期：", ""]
    if highs:
        for i, q in enumerate(highs, 1):
            lines.append(f"{i}. {q.question}")
    else:
        lines.append("（当前没有必须先拍板的高影响问题；若下面默认假设有出入请直接改。）")
    if mids:
        lines.append("")
        lines.append("下面几条如果没特别要求，我们就按默认做，你看一眼有没有问题：")
        for q in mids:
            head = q.question.split("？")[0]
            lines.append(f"· {head}？→ 默认：{q.default}")
    goal = card.goal.strip().rstrip("。")
    if not futures and _FUTURES_Q_HINT.search(goal):
        goal = _neutral_goal(card)
    lines += ["", f"我们初步理解是：{goal}。有出入的话随时纠正我。"]
    return "\n".join(lines)


def refine_with_llm(
    text_redacted: str,
    card: Card,
    questions: list[Question],
    llm: LLM,
    drafts: list | None = None,
    repos: list | None = None,
) -> tuple[Card, list[Question]]:
    """api 模式：润色标题/功能点并补问题；有 HTML/仓库代码时一并读懂。失败则原样返回。"""
    if llm.mode != "api":
        return card, questions
    from ..drafts import draft_context_for_llm, summarize

    has_materials = bool(drafts or repos)
    system = load_prompt("intake_refine") or "你是需求分析师。只输出 JSON。有材料时按材料提问，勿套期货模板。"
    payload = {
        "原话": text_redacted,
        "规则抽取": json.loads(card.to_json()),
        "已有问题": [q.question for q in questions],
        "有现有材料": has_materials,
        "提醒": (
            "已提供 HTML/Git：追问必须针对材料中的具体页面、字段、接口或模块；禁止无关期货模板。"
            if has_materials
            else "未提供材料：按原话领域提问；原话非期货业务时不要问期货口径。"
        ),
    }
    if has_materials:
        payload["现有系统材料"] = draft_context_for_llm(
            list(drafts or []),
            list(repos or []),
            html_limit=14000,
            code_limit=22000,
            keywords=card.keywords() | set(card.indicators) | set(card.features),
        )
        payload["材料摘要"] = summarize(list(drafts or []), list(repos or []))
    user = json.dumps(payload, ensure_ascii=False)
    try:
        data = llm.chat_json(system, user)
    except Exception:
        return card, questions
    if not isinstance(data, dict):
        return card, questions
    if isinstance(data.get("goal"), str) and data["goal"].strip():
        card.goal = data["goal"].strip().rstrip("。")
    if not card.title_from_template and isinstance(data.get("title"), str) and data["title"].strip():
        card.title = data["title"].strip()[:30]
    if isinstance(data.get("features"), list) and data["features"]:
        feats = [str(f).strip() for f in data["features"] if str(f).strip() and not re.search(r"上线|排期|周会|按计划|尽快", str(f))]
        if len(feats) >= 2:
            card.features = feats[:12]
            card.raw_features = (card.raw_features + [""] * 12)[: len(card.features)]
    extras: list[Question] = []
    for i, q in enumerate(data.get("extra_questions", [])[:4]):
        if isinstance(q, dict) and q.get("question"):
            extras.append(
                Question(
                    f"L{i + 1}",
                    q.get("category", "补充"),
                    "模型",
                    q.get("impact", "中"),
                    q["question"],
                    q.get("why", ""),
                    q.get("default", "待业务答复"),
                )
            )
    if extras and has_materials:
        # 有材料：模型问题优先；去掉未接地的期货模板，保留通用规则题作补充
        kept = [q for q in questions if q.tag != "期货"]
        questions = extras + kept
    elif extras:
        questions = list(questions) + extras
    blob = " ".join(card.features)
    for col in textutil.find_added_columns(blob) + textutil.find_indicators(blob):
        # 模型常把 sortino 写成 Sortino；与原话同词不同大小写只保留一份（保留先抽出的原话写法）
        textutil.merge_label(card.indicators, col)
    card.engine = "规则 + 模型"
    return card, questions

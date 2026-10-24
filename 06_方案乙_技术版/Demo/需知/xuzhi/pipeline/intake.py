"""问清：从一堆话里抽出需求卡片，生成待确认清单与确认消息。规则引擎兜底，LLM（若有）润色补充。"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

from .. import knowledge, textutil
from ..llm import LLM, load_prompt

TYPE_RULES: list[tuple[str, str, int]] = [
    (r"日报|周报|月报|报表|报告|一列|加一列|改版|定期|报送|对比表|汇总表|明细表|汇总成一张表|导出 ?Excel|简报", "报表", 2),
    (r"页面|看板|大屏|查询|展示|界面|H5|检索", "页面", 2),
    (r"提醒|预警|告警|通知|推送|提示|别漏", "提醒", 2),
    (r"接口|API|对接|调用", "接口", 2),
    (r"流程|审批|电子化|签字", "流程", 2),
    (r"抓取|采集|落库|测算|计算|识别|解析|接入", "数据", 1),
]
TRIGGER_RULES = [(r"公告|发布后|拒单|下单|报单|指令|异动|变动时|触发", "事件"),
                 (r"每天|每日|每个?交易日|日报|周报|每周|月报|每月|每个月|月初|定时|日终|结算后", "定时"), (r".*", "手动")]
# 先认时效（实时 / 事件后限时），再认周期；周期里「每周 / 每天 / 每月」压过报表名，最后才是「结算后」这种时点；单独的「周五」不算每周
FREQ_RULES = [(r"实时|盘中|下单前|报单前|秒级", "实时"), (r"公告发布后|\d+\s*分钟内", "事件后限时"), (r"分钟", "分钟级"),
              (r"每周|每个周", "每周"), (r"每天|每日|每个?交易日", "每日"), (r"每月|每个月|月初", "每月"), (r"周报", "每周"),
              (r"日报", "每日"), (r"月报", "每月"), (r"结算后|日终", "结算后"), (r".*", "按需")]
# 「30 分钟内」是时效不是上线时间；单独的「之前」也不是
DEADLINE_RE = re.compile(r"(下周|本周|下个?月|月底|年底|\d+\s*(?:天|日|工作日|周|个月)内|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?\s*之?前|尽快|越快越好|周会)")
NONFUNC_RULES = [(textutil.MOBILE + r"|移动|随时|出差", "手机端可看"), (r"留痕|事后查询|审计", "全程留痕可查"), (r"脱敏|信息安全|不该给的|客户版", "客户信息脱敏"),
                 (r"夜间|夜盘", "夜间 / 夜盘时段可用"), (r"\d+\s*分钟内|实时|盘中", "时效要求（实时 / 限时）"), (r"权限|只能看|谁能看", "数据权限控制"),
                 (r"排序|按风险", "结果按风险排序"), (r"导出|下载", "支持导出"), (r"部署|机器上|服务器", "部署环境约束")]
_REJECT = re.compile(r"^(领导|王总|谢谢|技术部同事)")
_EMPTY_CORE = re.compile(r"(?:在吗|谢谢|跟你说个事)?[，,。！!\s]*")
_VAGUE_NOTE = "原话信息不足，先确认对象与问题"

TITLE_TEMPLATES: list[tuple[str, str]] = [
    (r"净值日报.*对标指数", "净值日报改版：新增对标指数列"),
    (r"公告.*(汇总|简报|抽出来)", "交易所公告盘前简报（含持仓影响标注）"),
    (r"适当性.*(匹配|报送)|报送.*适当性", "客户适当性匹配报送表"),
    (r"套保.*(方案|测算)", "套保方案测算页面"),
    (r"(仓单|库存).*(周报|日报|报表)", "仓单库存周报"),
    # 只有同时要变动列和风险指标时才套这个标题；其它净值日报改动按抽到的列生成
    (r"净值日报(?=.*(?:变动|较上一))(?=.*风险指标).*(加|新增|放进|改)", "净值日报改版：新增变动列与风险指标"),
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
    recalled: str = ""  # 非空 = 答复来自追问记忆（沿用上次口径），值为来源与日期

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
    client_facing: bool = False   # 原话要给客户看（客户版、发给客户）；只是提到客户不算

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


_SUBJECT_RE = re.compile(r"^\s*(?:主题|标题)[:：]\s*(.+?)\s*$", re.M)
_GENERIC_SUBJECT = re.compile(r"^(?:[一二两三四五六七八九十几多]+[项个件条点]|若干)|^(?:系统|需求|问题|事项|申请|系统需求)$")
_GENERIC_CLAUSE = re.compile(r"^(?:页面|报表|看板|接口|功能|工具|系统|日报|周报|月报|提醒|流程|查询页面|监控页面)$")
_DELIVERABLE = re.compile(r"报表|日报|周报|月报|页面|看板|提醒|接口|流程|工具|清单|名单|台账|简报|查询|统计|比对|核对|电子化")


def _subject_title(raw: str) -> str:
    """邮件 / 需求单的「主题 / 标题」行，去掉「关于」「的需求」「需求说明」。"""
    m = _SUBJECT_RE.search(raw or "")
    if not m:
        return ""
    s = re.sub(r"^(?:关于|有关)", "", m.group(1).strip())
    s = re.sub(r"[（(]\s*(?:需求说明|需求)\s*[）)]$", "", s)
    s = re.sub(r"的?(?:需求说明|需求)$", "", s).strip(" ：:，,。")
    return "" if not s or _GENERIC_SUBJECT.search(s) else s[:30]


def _clause_title(feature: str) -> str:
    """功能点的第一个分句做标题：去掉问句与「我们想做个」这类说法，按标点截断。"""
    s = re.split(r"[，,。；;！!？?：:]", feature or "")[0]
    s = re.sub(r"(?:能不能|可不可以|能否|可否)(?:帮忙)?(?:做|搞|弄)?一?个?|我们想做一?个|希望做一?个|想做一?个", "", s)
    s = re.sub(r"^(?:想要|需要|要)?一?个", "", s)
    s = re.sub(r"[\"“”「」'‘’]", "", s)
    return re.sub(r"[吗呢]$", "", s).strip()


def _column_change_title(text: str) -> str:
    """净值日报的其它改动：按原话抽到的加 / 删 / 改名列生成标题，不套「变动列与风险指标」。"""
    added = textutil.find_added_columns(text)
    if added:
        return f"净值日报改版：新增{'、'.join(added[:2])}列"
    removed = textutil.find_removed_columns(text)
    if removed:
        return f"净值日报改版：去掉{'、'.join(removed[:2])}列"
    renamed = textutil.find_renamed_columns(text)
    if renamed:
        return f"净值日报改版：{renamed[0][0]}改为{renamed[0][1]}"
    return ""


def _title(text: str, req_type: str, card: Card, raw: str = "") -> str:
    changed = textutil.find_changed_indicators(text)
    if changed:
        return f"{'、'.join(changed)}计算口径调整"
    for pat, title in TITLE_TEMPLATES:
        if re.search(pat, text):
            card.title_from_template = True
            return title
    if "净值日报" in text:
        by_column = _column_change_title(text)
        if by_column:
            return by_column
    subject = _subject_title(raw or text)
    if subject:
        return subject
    obj = "、".join((card.indicators or card.symbols or card.scope_objects)[:2])
    noun = {"报表": "报表", "页面": "页面", "提醒": "提醒", "接口": "接口", "流程": "流程", "数据": "数据处理"}.get(req_type, "功能")
    clause = _clause_title(card.features[0]) if card.features else ""
    # 有指标 / 品种时，分句里得说到它们或交付物才当标题（「开盘前 5 分钟」只是时点）
    names = [*card.indicators, *card.symbols]
    telling = not names or any(n in clause for n in names) or bool(_DELIVERABLE.search(clause))
    if 4 <= len(clause) <= 24 and not _GENERIC_CLAUSE.match(clause) and telling:
        return clause
    if obj:
        return f"{obj}{noun}"
    return clause[:24] if len(clause) >= 4 else f"新{noun}需求"


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


def _sentence_core(s: str) -> str:
    """剥掉句首称呼与寒暄（最多三层：「小李在吗，跟你说个事」），剩下的才判断是不是功能点。"""
    core = s.strip()
    for _ in range(3):
        nxt = textutil.FILLER.sub("", core).strip()
        if nxt == core:
            break
        core = nxt
    return core


_SOURCE_AFTER = re.compile(r"(?:那边|系统|口径|的数据|每天都有|提供)")


def _only_data_source(role: str, text: str) -> bool:
    """「风控那边每天都有的」说的是数据从哪来，不是谁来用。"""
    ends = [m.end() for m in re.finditer(re.escape(role), text)]
    if not ends or not all(_SOURCE_AFTER.match(text, e) for e in ends):
        return False
    r = re.escape(role)
    return not re.search(rf"(?:给|发给|方便|让){r}|{r}(?:看|用|要)", text)


def _user_roles(text: str, body: str, own: str) -> list[str]:
    roles = []
    for r in textutil.USER_ROLES:
        if r not in text or r == "领导" or (own and r in own and r != own):
            continue
        if r in ("客户", "投资者") and not textutil.CLIENT_FACING.search(body):
            continue   # 只是提到客户（客户问过、客户张先生），不是给客户用
        if _only_data_source(r, text):
            continue
        roles.append(r)
    return roles


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
    changed_indicators = textutil.find_changed_indicators(body)
    card.indicators = list(changed_indicators)
    for indicator in textutil.find_indicators(body):
        textutil.merge_label(card.indicators, indicator)
    for col in textutil.find_added_columns(body):
        textutil.merge_label(card.indicators, col)
    card.scope_objects = _scope_objects(body)
    card.data_sources = textutil.match_rules(body, textutil.DATA_SOURCE_RULES)
    card.channels = textutil.match_rules(body, textutil.CHANNEL_RULES)
    m = DEADLINE_RE.search(body)
    card.deadline = m.group(1) if m else ""
    card.nonfunctional = textutil.match_rules(body, NONFUNC_RULES)
    card.client_facing = bool(textutil.CLIENT_FACING.search(body))
    own = card.requester.split("·")[-1] if card.requester else ""
    roles = _user_roles(text, body, own)
    card.users = ([own] if own and own not in roles else []) + roles or ["提出人"]
    for s in textutil.sentences(body):
        core = _sentence_core(s)
        if not core or _EMPTY_CORE.fullmatch(core) or _REJECT.match(s) or _REJECT.match(core):
            continue
        if not textutil.FEATURE_CUES.search(core) or textutil.BACKGROUND.search(core):
            continue
        if textutil.match_rules(core, NONFUNC_RULES) and not textutil.ACTION_CUES.search(core):
            continue   # 只有时效、夜间、信息安全这类约束：已记进非功能，不当「要做什么」
        f = textutil.clean_feature(core)
        # 长度影响展示，不应决定是否丢弃事实；长句常含公式、适用范围和否定约束。只丢以冒号收尾的引导语和过短的残句。
        if len(f) >= 6 and not f.rstrip().endswith(("：", ":")) and f not in card.features:
            card.features.append(f)
            card.raw_features.append(s)
    card.features, card.raw_features = card.features[:12], card.raw_features[:12]
    card.title = _title(body, card.req_type, card, raw=text)
    futures = looks_futures_domain(body, card.title, " ".join(card.features), " ".join(card.indicators))
    card.goal = _futures_goal(card) if futures else _neutral_goal(card)
    if changed_indicators:
        card.goal = f"按需求原话调整{'、'.join(changed_indicators)}的计算口径"
    card.assumptions = _assumptions(card, body)
    return card


_FUTURES_HINT = re.compile(
    r"期货|期权|合约|主力|基差|价差|结算|日终|夜盘|限仓|保证金|净值|"
    r"持仓|交割|品种|报单|下单|交易所|对标指数|资管产品|换月"
)


def looks_futures_domain(text: str, *extra: str) -> bool:
    blob = " ".join([text or "", *extra])
    return bool(_FUTURES_HINT.search(blob))


def card_is_futures(card: Card) -> bool:
    """只拿到卡片时（写单、出样）判断领域：看标题、功能点原句、指标与品种。"""
    return bool(card.symbols) or looks_futures_domain(
        card.title, " ".join(card.features), " ".join(card.raw_features), " ".join(card.indicators)
    )


def _added_column_names(text: str, card: Card) -> list[str]:
    """从原话与功能点抽出明确「加一列」的列名。"""
    cols: list[str] = []
    title = [] if card.title_from_template else [card.title or ""]   # 模板标题不是原话，不能从里面抽列名
    for snip in [text or ""] + title + list(card.features or []) + list(card.raw_features or []):
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
    ]
    if re.search(r"(?:放在|放到|挨着|紧跟)[^，,。；;、\s]{2,10}?的?(?:旁边|后面|右边|之后)", text or ""):
        qs = [q for q in qs if q.id != "C_POS"]   # 原话已说了放哪，不再问
    qs += [
        Question(
            "C_SIGN",
            "展示格式",
            "通用",
            "低",
            f"若{label}是数字，正负要不要用颜色区分（如红涨绿跌）？导出 Excel 是否与页面同一套格式？",
            "着色与导出格式常被当成「改了需求」。",
            "页面不强制着色；导出与页面小数位、百分号规则一致。",
        ),
    ]
    return qs


_VAGUE_WORDS = re.compile(r"那个|这个|优化|改进|完善|改一下|弄一下|调一下|处理一下|看一下|看看|搞一下")


def _too_vague(body: str, card: Card) -> bool:
    """没点出指标、数据来源、品种或对象，又不到 40 字，且说法含糊（「那个报表优化一下」）或抽不出功能点。
    「每月初提醒过生日的员工」这种短而具体的需求不算。"""
    if card.indicators or card.data_sources or card.symbols or card.scope_objects:
        return False
    if len(re.sub(r"\s+", "", body or "")) >= 40:
        return False
    return bool(_VAGUE_WORDS.search(body or "")) or not card.features


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
    if _too_vague(body, card):
        # 一句模糊的话也照样出整单会误导排期：先问清是哪个对象、哪里不好
        qs.insert(0, Question(
            "Q_VAGUE", "目标与使用", "通用", "高",
            "具体是哪张报表 / 哪个页面？现在的问题是什么（慢 / 错 / 缺字段 / 难用）？希望改成什么样？",
            "原话没说是哪个对象、哪里不好，方案和排期都无从下手。", "先和业务对一下现状与期望，再出需求单。",
        ))
        if _VAGUE_NOTE not in card.assumptions:
            card.assumptions.append(_VAGUE_NOTE)
    seen_ids = {q.id for q in qs}
    seen_cat: dict[str, int] = {}
    for q in qs:
        seen_cat[q.category] = seen_cat.get(q.category, 0) + 1
    probes = knowledge.probes()
    weekly = any(p.id == "T05W" and p.hits(body) for p in probes)   # 周报的比较基准是上周，不再问上一交易日
    for p in sorted(probes, key=lambda p: (knowledge.IMPACT_ORDER[p.impact], p.tag != "期货", p.triggers == (".*",))):   # 同档内：期货题优先，常驻通用题（.*）最后
        if not p.hits(body):
            continue
        if has_materials and p.tag == "期货" and not futures_ok:
            continue
        if not has_materials and p.tag == "期货" and not futures_ok:
            continue
        if p.id == "T29" and not card.deadline:
            continue
        if p.id == "T05" and weekly:
            continue
        if p.id in seen_ids:
            continue
        if seen_cat.get(p.category, 0) >= 3:
            continue
        seen_cat[p.category] = seen_cat.get(p.category, 0) + 1
        # 非期货需求用中性问法，免得通用题带出「品种 / 交易日 / 下单」
        question = p.question if futures_ok or not p.question_generic else p.question_generic
        default = p.default if futures_ok or not p.default_generic else p.default_generic
        qs.append(Question(p.id, p.category, p.tag, p.impact, question, p.why, default))
        seen_ids.add(p.id)
    # 加列场景优先保留展示格式题，再截断；截断后按影响排序，同档期货题在前（稳定排序，同档内保持原顺序）
    return sorted(qs[:limit], key=lambda q: (knowledge.IMPACT_ORDER.get(q.impact, 9), q.tag != "期货"))


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
    # 只看原话、标题、功能点和指标：题目文本自带期货词，算进来就会把非期货需求也判成期货
    futures = looks_futures_domain(text, card.title, " ".join(card.features), " ".join(card.indicators))
    usable = []
    for q in questions:
        if not futures and (q.tag == "期货" or _FUTURES_Q_HINT.search(q.question) or _FUTURES_Q_HINT.search(q.default)):
            continue
        usable.append(q)
    highs = [q for q in usable if q.impact == "高" and not q.answer.strip()]
    mids = [q for q in usable if q.impact != "高" and not q.answer.strip()]
    recalled = [q for q in usable if q.answer.strip() and getattr(q, "recalled", "")]
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
    if recalled:
        lines.append("")
        lines.append("这几条沿用你们上次的口径，这次有变化再告诉我：")
        for q in recalled:
            head = q.question.split("？")[0]
            lines.append(f"· {head}？→ 沿用：{q.answer.strip()}")
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
    from ..drafts import draft_context_for_llm, redact_material, summarize
    from ..privacy import labels_in

    has_materials = bool(drafts or repos)
    system = load_prompt("intake_refine") or "你是需求分析师。只输出 JSON。有材料时按材料提问，勿套期货模板。"
    # 材料的标签接在原话已有标签之后编号，模型不会把两处的 <手机_1> 当成同一个人
    mapping = {label: label for label in labels_in(text_redacted)}
    payload = {
        "原话": text_redacted,
        "规则抽取": json.loads(card.to_json()),
        # 追问里会带底稿表头（插在哪一列旁边），出站前同样过隐盾
        "已有问题": [redact_material(q.question, mapping) for q in questions],
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
            mapping=mapping,
        )
        payload["材料摘要"] = redact_material(summarize(list(drafts or []), list(repos or [])), mapping)
    user = json.dumps(payload, ensure_ascii=False)
    try:
        llm.current_purpose = "问清润色"   # 审计用途；假客户端也能接住
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
    raw_extras = data.get("extra_questions")
    if not isinstance(raw_extras, list):
        raw_extras = []
    for i, q in enumerate(raw_extras[:4]):
        if isinstance(q, dict) and isinstance(q.get("question"), str) and q["question"].strip():
            impact = str(q.get("impact") or "中").strip()
            impact = {"high": "高", "medium": "中", "low": "低"}.get(impact.lower(), impact)
            if impact not in ("高", "中", "低"):
                impact = "中"
            extras.append(
                Question(
                    f"L{i + 1}",
                    str(q.get("category") or "补充"),
                    "模型",
                    impact,
                    q["question"].strip(),
                    str(q.get("why") or ""),
                    str(q.get("default") or "待业务答复"),
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

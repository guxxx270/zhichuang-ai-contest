"""纪要直通车：一段会议纪要 / 长消息里有几条互不相干的需求时，按规则拆开，每条单独走流水线、各记一行账。

只用确定性规则，不调模型（毫秒级、可复现）：
1. 头部元信息（来源 / 时间 / 记录 / 主题 / 标题 / 日期）每条都带上，来源识别照常；参会名单不带——名单里的部门会被认成提出方；
2. 切分点按优先级：行首编号条目 → 部门开头（「风控部提出：」「研究所：」「资管部希望」）→ 句首连接词（还有一件事 / 第二件事 / 另外…）；
   编号只在像纪要、开头说了「几件事」或至少两条编号各带提出方时才切：需求单里的 1. 2. 3. 是同一条需求的功能点；
   低一级或缩进更深的编号、紧跟在「谁要做什么」那句后面的列表、「××部补充：」都算上一条的要点，不另起一条；
3. 一段像需求才算一条：有需求动词、不少于 12 字、不是背景 / 参会 / 「下周再议」这类决议；太短的并回上一条；最多 8 条；
4. 不足 2 条返回 []，照常按一条需求处理。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import textutil

MAX_SEGMENTS = 8
MIN_CHARS = 12

# 头部元信息每条都带上；参会类名单整行丢掉
_META = re.compile(r"^\s*(?:来源|时间|日期|记录|记录人|主题|标题|会议|地点)\s*[:：]")
_ATTEND = re.compile(r"^\s*(?:参会|出席|列席|与会|参加|缺席|请假|主持)(?:人员|人|部门|领导|名单)?\s*[:：]")
_MINUTES_HEAD = re.compile(r"纪要|会议|晨会|周会|例会|碰头会|评审会|专题会|议题")
_MINUTES_BODY = re.compile(r"纪要|会上|与会|参会|议题")   # 正文里只认这几个：「领导周会要看」不算纪要
_MULTI = re.compile(r"(?:两|三|四|五|六|七|八|几)(?:件事|个事|个需求|项需求|条需求|点需求|件需求)"
                    r"|以下(?:几|两|三|四|五|六)(?:个|项|条|件)(?:需求|事项|事)")

_NUM = re.compile(r"^\s*(?:\d{1,2}\s*[.．、)）](?!\d)|[（(]\d{1,2}[)）]|[一二三四五六七八九十]{1,3}\s*[、.．]"
                  r"|[（(][一二三四五六七八九十]{1,3}[)）]|[①-⑳]|[-•·*](?!\d))\s*")
# 「二、需技术部支持的事项」这类整行短标题：分小节，本身不是需求
_HEADING = re.compile(r"^\s*(?:[一二三四五六七八九十]{1,3}\s*[、.．]|[（(][一二三四五六七八九十]{1,3}[)）])"
                      r"\s*[^。；;！!？?，,：:]{0,16}[：:]?\s*$")
_INTRO = re.compile(r"(?:[：:]|如下)\s*$")   # 「要求如下：」后面不带提出方的编号是这一条的要点
# 「经会上讨论，需求如下：」这类引出要点的行本身没有需求动词，也照样收要点；「各部门需求如下：」是好几条，不收
_POINTS_INTRO = re.compile(r"(?:要求|需求|功能|要点|明细|细节|口径)(?:如下|包括|有)?\s*[：:]\s*$")
_PLURAL = re.compile(r"各(?:部门|方|位|组|条线)|大家|几(?:个|件|项|条|点)|多(?:个|项|条)|[部所]和[^，,。：:]{1,6}[部所]")

_DEPT_OF = dict(textutil.DEPARTMENTS)
_DEPT_KEYS = "|".join(sorted((re.escape(k) for k in _DEPT_OF), key=len, reverse=True))
# 部门（+ 人）+ 提出 / 希望 / 冒号；「补充：」「附议：」不算换人，「提醒 / 提示」不是「提」
_SAYS = (r"[^，,。；;！!？?：:]{0,12}?\s*(?:提出|希望|反馈|反映|要求|表示|建议|想要|想|需要|提(?![醒示供交前高升])|说|问"
         r"|(?<!补充)(?<!附议)(?=[：:]))")
_LEAD = re.compile(rf"^(?P<dept>{_DEPT_KEYS}){_SAYS}")
# 词典外的部门开头（「结算部需要」「上海营业部提出：」）：本条自己点了提出方，不再借上一条 / 小节 / 头部的。
# 部门名前至少两个字（「全部 / 内部」不算），「业务部门 / 相关部门」「部长 / 部署 / 部分」这类也不算
_UNIT_LEAD = re.compile(rf"^(?!我们|你们|他们|咱们)(?P<unit>[\u4e00-\u9fff]{{2,6}}?部)(?![门分长署件位]){_SAYS}")
_NOT_UNIT = re.compile(r"[内外全局底顶头尾东西南北中上下前后本细大小]部$")   # 「公司内部需要…」不是部门
# 「营业部补充：…」「风控部李经理补充…」接着上一条说，不另起一条；「补充一个需求」才是新的，「需要补充一份模板」也不算
_ADDON = re.compile(rf"^(?:{_DEPT_KEYS}|[\u4e00-\u9fff]{{2,6}}?部)[^，,。；;！!？?：:]{{0,6}}?(?:补充|附议)"
                    r"(?!一?(?:个|条|项|件|点)?新?的?需求)")
_KNOWN = set(_DEPT_OF.values())   # extract_card 认得出的提出方
_FILLER = re.compile(r"^(?:对了|然后|同时|其次|最后|另外|此外|还有|再就是|顺便)[，,、\s]*")

_CUE_STRONG = re.compile(r"^(?:(?:还有|另外|另|再)(?:一|有一)?(?:件|个)(?:事情|事|需求)"
                         r"|第[一二三四五六七八九十](?:件事|个需求|个事|点需求)"
                         r"|另外还有一?(?:件|个)(?:事情|事|需求))[，,、：:\s]*")
_CUE_WEAK = re.compile(r"^(?:另外|此外|再就是|还有就是|顺便|还有)[，,、：:\s]*")   # 这几个也常引出同一需求的补充
_NEW_THING = re.compile(r"(?:做|搞|弄|开发|建|上|加|要|出)一?个|新做|另做|再做|新建|新开")
_DELIVERABLE = re.compile(r"日报|周报|月报|报表|报告|简报|页面|看板|大屏|提醒|预警|告警|通知|推送|接口|流程|审批|功能|工具|模块"
                          r"|清单|台账|查询|监控|测算|导出|短信|对比表|汇总表|明细表")
# 弱连接词后要「另做一个新交付物」才算新需求；「另外再做个导出功能」仍是同一条的补充
_NEW_DELIVERABLE = re.compile(r"日报|周报|月报|报表|简报|页面|看板|大屏|提醒|预警|告警|接口|流程|台账|小程序")
_ASK = re.compile(r"能不能|可不可以|能否|可否")
_DECISION = re.compile(r"(?:下周|下次|会后|改天|择期|另行|以后|后续|下回)?再议|另行(?:讨论|安排|研究)|再(?:讨论|研究)"
                       r"|暂缓|暂不(?:做|考虑|处理|安排|排期|开发)|搁置|不在本期|延后讨论")
_NOT_FEATURE = re.compile(r"^(?:领导|王总|谢谢|技术部同事|小李|在吗)")   # 与 extract_card 判功能点同一口径


@dataclass
class Segment:
    index: int          # 第几条（从 1 起）
    text: str           # 头部元信息 + 本条正文，可直接交给 analyze()
    body: str           # 本条正文
    title_hint: str     # 预览（≤ 30 字）
    requester: str      # 本条提出方：开头点名的部门（词典外的照原话）；没点名的沿用小节标题 / 开场白 / 头部，最后才看段内其它位置
    reason: str         # 为什么在这里切：首段 / 编号条目 / 提出方切换 / 连接词


@dataclass
class _Piece:
    line: int
    text: str
    first: bool         # 是否行首


@dataclass
class _Chunk:
    kind: str           # 首段 / 编号条目 / 提出方切换 / 连接词 / 小标题
    lead: str = ""
    unit: str = ""          # 开头点了词典外的部门：本条自己的提出方，不借别处的
    pieces: list[_Piece] = field(default_factory=list)
    has_req: bool = False   # 已经有像需求的句子（逐句累计，长文不必反复重扫）
    multi: bool = False     # 说了「两件事 / 以下三个需求」
    level: int = 0          # 开头编号的层级（见 _level）；没有编号为 0
    indent: int = 0         # 开头那行的缩进
    item: bool = False      # 小标题本身就是一条需求（「一、风控部提出希望做一个追保提醒」）
    concrete: bool = False  # 明确要做个什么 / 问了「能不能」：后面紧跟的列表多半是它的要点


def _dept_in(text: str) -> str:
    """文中最早出现的部门（与 extract_card 认提出方同一口径）。"""
    hits = [(text.find(k), v) for k, v in textutil.DEPARTMENTS if k in text]
    return min(hits)[1] if hits else ""


def _dept_key(requester: str) -> str:
    """提出方 → 能被 extract_card 认回同一提出方的词（资管部·投资经理 → 投资经理）。"""
    if requester in _DEPT_OF and _DEPT_OF[requester] == requester:
        return requester
    return next((k for k, v in textutil.DEPARTMENTS if v == requester), requester)


def _lead(s: str) -> str:
    """句首「部门（+ 人）+ 提出 / 希望 / ：」→ 提出方；没有返回空。"""
    m = _LEAD.match(_FILLER.sub("", s.strip(), count=1))
    return _DEPT_OF[m.group("dept")] if m else ""


def _unit(s: str) -> str:
    """句首点了词典外的部门 → 该部门（名字里带词典部门的按词典口径：上海营业部 → 营业部）；没有返回空。"""
    m = _UNIT_LEAD.match(_FILLER.sub("", s.strip(), count=1))
    if not m or _NOT_UNIT.search(m.group("unit")):
        return ""
    return _dept_in(m.group("unit")) or m.group("unit")


_LEVELS = [re.compile(p) for p in (r"[一二三四五六七八九十]", r"[（(][一二三四五六七八九十]", r"\d", r"[（(]\d", r"[①-⑳]")]


def _level(marker: str) -> int:
    """编号层级：一、→（一）→ 1. →（1）→ ① → - •，数越大越低一级。"""
    m = marker.strip()
    return next((i for i, pat in enumerate(_LEVELS, 1) if pat.match(m)), len(_LEVELS) + 1)


def _size(s: str) -> int:
    return len(re.sub(r"[\W_]+", "", s))


def _req_sentences(text: str) -> list[str]:
    return [s for s in textutil.sentences(text)
            if textutil.FEATURE_CUES.search(s) and not textutil.BACKGROUND.search(s) and not _NOT_FEATURE.match(s)]


def _standalone(text: str) -> bool:
    """自成一件事：点了交付物、要做个新东西或明确在问「能不能」；否则多半是上一条的要点。"""
    return bool(_DELIVERABLE.search(text) or _NEW_THING.search(text) or _ASK.search(text))


def _pieces(lines: list[str]) -> list[_Piece]:
    out: list[_Piece] = []
    for i, line in enumerate(lines):
        first = True
        for m in re.finditer(r"[^。；;！!？?]+[。；;！!？?]*|[。；;！!？?]+", line):
            if m.group(0).strip():
                out.append(_Piece(i, m.group(0), first))
                first = False
    return out


def _join(pieces: list[_Piece]) -> str:
    out, last = "", None
    for p in pieces:
        out += ("\n" if last is not None and p.line != last else "") + (p.text.strip() if p.first else p.text)
        last = p.line
    return out.strip()


def _head(lines: list[str]) -> tuple[list[str], list[str]]:
    """开头连续的元信息行 → 头部；参会名单不进任何一条。"""
    header: list[str] = []
    body: list[str] = []
    in_head = True
    for line in lines:
        if _ATTEND.match(line):
            continue
        if in_head and (not line.strip() or _META.match(line)):
            if line.strip():
                header.append(line.strip())
            continue
        in_head = False
        body.append(line)
    return header, body


def is_minutes(text: str) -> bool:
    """头部写了纪要 / 会议，或正文提到纪要、会上、参会：按纪要的习惯切。"""
    header, _ = _head((text or "").splitlines())
    return bool(_MINUTES_HEAD.search("\n".join(header)) or _MINUTES_BODY.search(text or ""))


def source_hint(text: str) -> str:
    """拆开后每条的来源提示：纪要类给「会议纪要」，其余交给 analyze 自动识别。"""
    return "会议纪要" if is_minutes(text) else ""


def _title_hint(body: str, limit: int = 30) -> str:
    sents = _req_sentences(body) or textutil.sentences(body) or [body]
    s = _CUE_STRONG.sub("", sents[0], count=1)
    m = _LEAD.match(s) or _UNIT_LEAD.match(s)
    if m:
        s = s[m.end():]
    s = textutil.clean_feature(s.lstrip("：:，, "))
    s = re.sub(r"^(?:要|做|搞|弄|加|出)?一?个(?![人股性税])", "", s)   # 点名后剩下的「要一个」「做个」
    s = re.sub(r"\s+", " ", s).strip("：:，,。 ") or body.strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _chunks(body_lines: list[str], gated: bool, head_dept: str) -> list[_Chunk]:
    chunks: list[_Chunk] = []
    cur: _Chunk | None = None
    intro = False   # 上一段以「：/ 如下」收尾，且没说几件事
    for p in _pieces(body_lines):
        s = p.text.strip()
        num = _NUM.match(s) if p.first else None
        core = s[num.end():] if num else s
        lead = _lead(core)
        level = _level(num.group(0)) if num else 0
        indent = len(p.text.expandtabs(4)) - len(p.text.expandtabs(4).lstrip()) if p.first else 0
        # 要点不另起一条：比所在条目低一级的编号；同级但缩进更深、又没另提新东西的；
        # 紧跟在「谁要做什么」那句后面、自己不点名也不另提新东西的列表
        # （那句得点了提出方或明确要做个什么：「本次讨论了各线需求，需要技术部评估」后面的编号照常各算一条）
        asks = bool(_NEW_THING.search(core) or _ASK.search(core))
        sub = bool(num) and not lead and cur is not None and (
            (cur.level > 0 and (cur.kind == "编号条目" or cur.item)
             and (level > cur.level or (indent > cur.indent and not asks)))
            or (cur.kind in ("首段", "提出方切换", "连接词") and cur.has_req and not cur.multi
                and (cur.lead or cur.unit or cur.concrete) and not asks))
        if p.first and (not num or lead):
            intro = False   # 编号段落结束，或换了人
        kind = ""
        if sub or (cur is not None and not lead and _ADDON.match(core)):
            pass   # 要点、「营业部补充：」：并进当前这条
        elif p.first and gated and _HEADING.match(s):
            kind, lead, intro = "小标题", "", False
        elif num and gated and not (intro and not lead):
            kind = "编号条目"
        elif lead and lead != ((cur.lead if cur else "") or head_dept) and (p.first or gated):
            kind = "提出方切换"   # 行首点名换人；像纪要时句首也算
        elif cur is not None and cur.has_req:
            strong = _CUE_STRONG.match(core)
            if strong:
                kind, lead = "连接词", lead or _lead(core[strong.end():])
            else:
                weak = _CUE_WEAK.match(core)
                if weak and (lead or (_NEW_THING.search(core) and _NEW_DELIVERABLE.search(core))):
                    kind, lead = "连接词", lead or _lead(core[weak.end():])
        if kind or cur is None:
            cur = _Chunk(kind or "首段", lead if kind != "小标题" else "", level=level, indent=indent)
            if kind != "小标题" and not cur.lead:
                cur.unit = _unit(_CUE_WEAK.sub("", _CUE_STRONG.sub("", core, count=1), count=1))
            chunks.append(cur)
        cur.pieces.append(p)
        cur.has_req = cur.has_req or bool(_req_sentences(s))
        cur.multi = cur.multi or bool(_MULTI.search(s))
        cur.concrete = cur.concrete or bool(_NEW_THING.search(s) or _ASK.search(s))
        if kind == "小标题":
            cur.item = cur.has_req and _size(core) >= MIN_CHARS
        # 先说了要做什么、再「要求如下：」才收要点（「需求如下：」本身就是在引出要点）；
        # 「会议决定：」「风控部提出两件事：」「各部门需求如下：」后面的编号照常各算一条
        if kind != "小标题" and _INTRO.search(s) and not cur.multi and (
                cur.has_req or (_POINTS_INTRO.search(s) and not _PLURAL.search(s))):
            intro = True
    return chunks


def _strip_start(chunk: _Chunk, body: str) -> str:
    """去掉本条开头的编号与「还有一件事，」这类连接词；原话其余不动。"""
    if chunk.kind in ("编号条目", "小标题"):
        body = _NUM.sub("", body, count=1)
    if chunk.kind == "连接词":
        body = _CUE_STRONG.sub("", body, count=1)
        body = _CUE_WEAK.sub("", body, count=1)
    return body.strip()


def split_requirements(text: str, *, min_segments: int = 2, max_segments: int = MAX_SEGMENTS) -> list[Segment]:
    """识别到不少于 min_segments 条需求时返回各条；否则返回 []（按一条需求照常处理）。"""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub("[\u200b-\u200d\u2060\ufeff]", "", raw)   # 网页 / 微信复制来的零宽字符会挡住行首编号和部门
    header, body_lines = _head(raw.split("\n"))
    head_text = "\n".join(header)
    head_dept = _dept_in(head_text)
    numbered_leads = sum(1 for line in body_lines if (m := _NUM.match(line)) and _lead(line[m.end():]))
    gated = is_minutes(raw) or bool(_MULTI.search(raw)) or numbered_leads >= 2

    found: list[dict] = []
    context = ""   # 小节标题 / 开场白点名的提出方，给后面没点名的条目；不从别的条目里借
    for chunk in _chunks(body_lines, gated, head_dept):
        body = _join(chunk.pieces)
        if chunk.kind == "小标题":
            context = _dept_in(body.split("\n", 1)[0])
            title = _NUM.sub("", body, count=1)
            if "\n" not in body and not (_req_sentences(title) and _size(title) >= MIN_CHARS):
                continue   # 只有小标题一行：分节用，不并进上一条；「一、净值日报加一列较上一交易日变动」本身就是一条
        body = _strip_start(chunk, body)
        if not body:
            continue
        if (chunk.kind in ("编号条目", "小标题", "连接词") and not chunk.lead and _DECISION.search(body)
                and not (_ASK.search(body) or _NEW_THING.search(body))):
            continue   # 「下周再议」这类决议不算需求；「能不能做个…，具体字段下周再议」仍是一条
        is_req = bool(_req_sentences(body)) and _size(body) >= MIN_CHARS
        if is_req and (chunk.kind in ("首段", "连接词") or not found or _standalone(body)):
            # 段内别处出现的部门多是收件人（「提醒客户经理」），排在头部之后
            requester = (chunk.lead or chunk.unit or (found[-1]["requester"] if chunk.kind == "连接词" and found else "")
                         or context or head_dept or _dept_in(body))
            found.append({"body": body, "requester": requester, "reason": chunk.kind})
        elif found and (_req_sentences(body) or chunk.kind in ("提出方切换", "连接词")):
            found[-1]["body"] += "\n" + body   # 太短、是上一条的要点或补充：并回上一条
        elif not found and chunk.kind != "小标题":
            context = chunk.lead or chunk.unit or _dept_in(body) or context   # 开场白「风控部提出两件事：」「以下是研究所的需求」
        # 其余（背景、参会、决议、落款）不进任何一条

    if len(found) < min_segments:
        return []
    out: list[Segment] = []
    for i, item in enumerate(found[:max_segments], 1):
        body, requester = item["body"].strip(), item["requester"]
        seg_text = head_text + ("\n\n" if head_text else "") + body
        if requester in _KNOWN and _dept_in(seg_text) != requester:
            # 头部点名的部门会抢在本条前面被认成提出方：首行写明本条提出方（词典外的部门认不出，写了也没用，不写）
            seg_text = f"提出方：{_dept_key(requester)}\n" + seg_text
        reason = item["reason"] if item["reason"] != "小标题" else "编号条目"
        out.append(Segment(i, seg_text, body, _title_hint(body), requester, reason))
    return out

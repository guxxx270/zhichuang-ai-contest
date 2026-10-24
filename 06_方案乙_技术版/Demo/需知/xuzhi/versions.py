"""需求版本对比：需求会变——受理、业务答复重算、模型增强、模型估时、需求变更时各存一版紧凑快照，
任选两版看改了什么（功能点、要素、待确认、工时、数据地图、IT 决策）、哪些要重新确认，并出一页变更说明（Markdown）。

快照只存分析结果和原话指纹（去空白后的哈希，不存原话）；对比纯程序、不调模型。
工时：模型估时只和模型估时比，规则参考只和规则参考比，两路数字绝不互相加减。
快照另记引擎（规则初稿 / 含模型结果），不算差异，只用来挑可比的版本：配了模型时一次答复重算或需求变更会先存规则初稿、
模型补全后再存一版，隔类相比会把「模型改写 ↔ 规则抽取」的说法差别当成需求变更。"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

CARD_FIELDS = ("title", "req_type", "requester", "goal", "features", "indicators", "symbols", "data_sources", "channels",
               "deadline", "nonfunctional")
# 要素对比：快照里存的卡片要素（功能点另算）都比，免得存了新版却显示「内容无变化」
FIELD_LABELS = (("title", "标题"), ("req_type", "类型"), ("requester", "提出方"), ("goal", "一句话理解"), ("indicators", "指标"),
                ("symbols", "品种"), ("data_sources", "数据来源"), ("channels", "渠道"), ("deadline", "期望时间"), ("nonfunctional", "非功能"))
TRACKS = (("before", "以前（人工开发）"), ("after", "现在（AI协同）"))
TRACK_SHORT = {"before": "以前", "after": "现在"}
DATA_FIELDS = ("need", "system", "interface", "freshness", "status")
DATA_LABELS = {"system": "来源系统", "interface": "接口", "freshness": "时效", "status": "状态"}
# 数据地图里要找人确认的状态 → 为什么要确认
ATTENTION = {
    "需申请": "要向数据负责团队申请接口权限",
    "改动受控": "接口改动受控，要走变更审批",
    "需新建": "系统目录里没有现成来源，要新建或外部接入",
    "时效不符": "需求要盘中 / 下单前可用，现有源是日终或 T+1：改用实时源，或与业务商定降级为日终口径",
}
_MODEL_STATUS = {"ready": "已出结果", "failed": "调用未成功", "unavailable": "未调用模型"}
_REWORD_RATIO = 0.5   # 两条功能点相似度过半，算同一条改了说法，不算一删一增
MIXED_NOTE = "两版一份是规则初稿、一份含模型结果：功能点、标题、一句话的差别多是模型改写的说法，删掉的功能点不列入需重新确认。"


def _num(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value) -> float | None:
    number = _num(value)
    return None if number is None else round(number, 2)


def fmt_num(value) -> str:
    number = _num(value)
    return "—" if number is None else f"{round(number, 2):g}"


def text_sig(text: str) -> str:
    """原话指纹：去掉空白后的脱敏原话取哈希，只用来判断原话改没改，不可还原原文。"""
    body = re.sub(r"\s+", "", text or "")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16] if body else ""


# ---------------------------------------------------------------- 快照
def _estimate_part(est) -> dict:
    """模型区间只在模型估时成功时记（换成小时）；规则参考只记以前 / 现在的参考值；历史参考只记条数。"""
    status = str(getattr(est, "status", "") or "unavailable")
    days = {key: _num(getattr(est, key, None)) for key in ("low", "mid", "high", "ai_low", "ai_mid", "ai_high")}
    model = None
    if status == "ready" and getattr(est, "method", "") == "llm" and all(v is not None for v in days.values()):
        model = {"before": {k: round(days[k] * 8, 2) for k in ("low", "mid", "high")},
                 "after": {k: round(days["ai_" + k] * 8, 2) for k in ("low", "mid", "high")}}
    refs = getattr(est, "references", None) or {}
    rule = refs.get("rule") or {}
    rule_part = None
    if rule.get("status") == "ready":
        before, after = _round((rule.get("before") or {}).get("mid")), _round((rule.get("after") or {}).get("mid"))
        if before is not None or after is not None:
            rule_part = {"before": before, "after": after}
    history = refs.get("history") or {}
    n_history = len(history.get("items") or []) if history.get("status") == "ready" else 0
    return {"status": status, "model": model, "rule": rule_part, "history": n_history}


def snapshot(analysis) -> dict:
    """一版的紧凑快照（纯 JSON）：需求卡要素、待确认清单与答复、工时、数据地图、IT 决策。"""
    card = analysis.card
    snap: dict = {}
    for key in CARD_FIELDS:
        value = getattr(card, key, "")
        snap[key] = [str(x) for x in value] if isinstance(value, (list, tuple)) else str(value or "")
    snap["text_sig"] = text_sig(getattr(analysis, "redacted_text", "") or "")
    snap["engine"] = str(getattr(analysis, "engine", "") or "")
    snap["questions"] = [
        {"id": str(q.id), "question": str(q.question), "impact": str(q.impact),
         "answer": str(q.answer or "").strip(), "default": str(q.default or "")}
        for q in getattr(analysis, "questions", None) or []
    ]
    snap["estimate"] = _estimate_part(analysis.estimate)
    arch = analysis.architecture
    snap["data_map"] = [{key: str(getattr(row, key, "") or "") for key in DATA_FIELDS}
                        for row in getattr(arch, "data_map", None) or []]
    snap["decisions"] = [{"id": str(d.id), "topic": str(d.topic), "recommended": str(d.recommended)}
                         for d in getattr(arch, "decisions", None) or []]
    return snap


def kind(snap: dict | None) -> str:
    """这一版是「规则」初稿还是含「模型」结果；早于引擎记录的快照为空。"""
    engine = str((snap or {}).get("engine") or "")
    return "模型" if "模型" in engine else ("规则" if engine else "")


# ---------------------------------------------------------------- 对比
@dataclass
class Diff:
    """两版差异：各节增删改 + 需重新确认 + 一句话概要；to_dict() 给接口返回。"""
    v_old: int = 0
    v_new: int = 0
    text_changed: bool = False
    mixed: bool = False   # 一版是规则初稿、一版含模型结果：功能点、标题、一句话的差别多是说法不同
    features: dict = field(default_factory=lambda: {"added": [], "removed": [], "reworded": []})
    fields: list = field(default_factory=list)
    questions: dict = field(default_factory=lambda: {"new": [], "dropped": [], "answered": [], "changed": [], "reworded": []})
    estimate: dict = field(default_factory=dict)
    data_map: dict = field(default_factory=lambda: {"added": [], "removed": [], "changed": []})
    decisions: dict = field(default_factory=lambda: {"changed": [], "added": [], "removed": []})
    reconfirm: list = field(default_factory=list)
    summary: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.text_changed or any(self.features.values()) or self.fields or any(self.questions.values())
                    or self.estimate.get("changed") or any(self.data_map.values()) or any(self.decisions.values()))

    def to_dict(self) -> dict:
        out = asdict(self)
        out["changed"] = self.changed
        return out


def _feature_diff(old: list[str], new: list[str]) -> dict:
    """功能点按原文比；删一条又加一条且说法相近的，算「改写」（同一功能点换了说法），不进需重新确认。"""
    added = [f for f in new if f not in old]
    removed = [f for f in old if f not in new]
    scored = sorted(((SequenceMatcher(None, r, a).ratio(), i, j) for i, r in enumerate(removed) for j, a in enumerate(added)),
                    key=lambda x: (-x[0], x[1], x[2]))
    used_r, used_a, pairs = set(), set(), []
    for ratio, i, j in scored:
        if ratio < _REWORD_RATIO:
            break
        if i in used_r or j in used_a:
            continue
        used_r.add(i)
        used_a.add(j)
        pairs.append((j, {"old": removed[i], "new": added[j]}))
    return {"added": [a for j, a in enumerate(added) if j not in used_a],
            "removed": [r for i, r in enumerate(removed) if i not in used_r],
            "reworded": [pair for _, pair in sorted(pairs, key=lambda x: x[0])]}


def _field_diff(old: dict, new: dict) -> list[dict]:
    out = []
    for key, label in FIELD_LABELS:
        o, n = old.get(key), new.get(key)
        if isinstance(o, list) or isinstance(n, list):
            o, n = list(o or []), list(n or [])
            added, removed = [x for x in n if x not in o], [x for x in o if x not in n]
            if added or removed:
                out.append({"key": key, "label": label, "old": o, "new": n, "added": added, "removed": removed})
        elif (o or "") != (n or ""):
            out.append({"key": key, "label": label, "old": o or "", "new": n or "", "added": [], "removed": []})
    return out


def _unstable(qid: str) -> bool:
    # 模型临时补问（L1…）每次编号重排：同号不同题就是另一道题
    return qid.startswith("L")


def _question_diff(old_qs: list[dict], new_qs: list[dict]) -> dict:
    olds = {q.get("id"): q for q in old_qs}
    news = {q.get("id"): q for q in new_qs}
    out = {"new": [], "dropped": [], "answered": [], "changed": [], "reworded": []}

    def same(qid) -> bool:
        p, q = olds.get(qid), news.get(qid)
        return p is not None and q is not None and not (_unstable(qid) and p.get("question") != q.get("question"))

    for qid, q in news.items():
        if not same(qid):
            out["new"].append(dict(q))
            continue
        p = olds[qid]
        old_ans, new_ans = (p.get("answer") or "").strip(), (q.get("answer") or "").strip()
        if any(p.get(k, "") != q.get(k, "") for k in ("question", "impact", "default")):
            out["reworded"].append({"id": qid, "impact": q.get("impact", ""), "old": p.get("question", ""),
                                    "new": q.get("question", ""), "answer": new_ans, "old_impact": p.get("impact", ""),
                                    "old_default": p.get("default", ""), "default": q.get("default", "")})
        if new_ans and not old_ans:
            out["answered"].append({"id": qid, "question": q.get("question", ""), "impact": q.get("impact", ""), "answer": new_ans})
        elif old_ans and old_ans != new_ans:
            out["changed"].append({"id": qid, "question": q.get("question", ""), "impact": q.get("impact", ""),
                                   "old": old_ans, "new": new_ans, "default": q.get("default", "")})
    out["dropped"] = [dict(p) for qid, p in olds.items() if not same(qid)]
    return out


def _delta(old, new) -> tuple[float | None, float | None]:
    old, new = _num(old), _num(new)
    if old is None or new is None:
        return None, None
    delta = round(new - old, 2)
    return delta, (round(100 * delta / old, 1) if old else None)


def _range(track: dict | None) -> str:
    track = track or {}
    if _num(track.get("low")) is None or _num(track.get("high")) is None:
        return ""
    return f"{fmt_num(track['low'])}～{fmt_num(track['high'])}"


def _estimate_diff(old: dict, new: dict, v_old: int, v_new: int) -> dict:
    """模型与规则各比各的：一边没有有效模型结果时只并列，不算增减。"""
    om, nm = old.get("model"), new.get("model")
    model = {"old_status": old.get("status", ""), "new_status": new.get("status", ""), "rows": [], "note": "", "changed": False}
    if om or nm:
        if om and nm and om == nm:
            pass
        else:
            model["changed"] = True
            for track, label in TRACKS:
                o, n = (om or {}).get(track) or {}, (nm or {}).get(track) or {}
                delta, pct = _delta(o.get("mid"), n.get("mid")) if om and nm else (None, None)
                model["rows"].append({"track": track, "label": label, "old": _round(o.get("mid")), "new": _round(n.get("mid")),
                                      "old_range": _range(o), "new_range": _range(n), "delta": delta, "pct": pct})
            if not om:
                model["note"] = f"v{v_old} 没有有效模型估时，v{v_new} 新出结果，两版不算增减。"
            elif not nm:
                state = _MODEL_STATUS.get(new.get("status", ""), "无结果")
                model["note"] = f"v{v_new} 没有有效模型估时（{state}），v{v_old} 的数字只作对照，不算增减。"
    orl, nrl = old.get("rule"), new.get("rule")
    rule = {"rows": [], "note": "", "changed": False}
    if (orl or nrl) and orl != nrl:
        rule["changed"] = True
        for track, label in TRACKS:
            o, n = (orl or {}).get(track), (nrl or {}).get(track)
            delta, pct = _delta(o, n)
            rule["rows"].append({"track": track, "label": label, "old": _round(o), "new": _round(n), "delta": delta, "pct": pct})
        if not orl:
            rule["note"] = f"v{v_old} 没有规则参考。"
        elif not nrl:
            rule["note"] = f"v{v_new} 没有规则参考。"
    history = {"old": int(old.get("history") or 0), "new": int(new.get("history") or 0)}
    return {"model": model, "rule": rule, "history": history,
            "changed": model["changed"] or rule["changed"] or history["old"] != history["new"]}


def _data_diff(old_rows: list[dict], new_rows: list[dict]) -> dict:
    olds, news = {}, {}
    for row in old_rows:
        olds.setdefault(row.get("need", ""), row)
    for row in new_rows:
        news.setdefault(row.get("need", ""), row)
    changed = []
    for need, row in news.items():
        prev = olds.get(need)
        if prev is None:
            continue
        diffs = {key: [prev.get(key, ""), row.get(key, "")] for key in DATA_FIELDS[1:] if prev.get(key, "") != row.get(key, "")}
        if diffs:
            changed.append({"need": need, "changes": diffs, "old": dict(prev), "new": dict(row)})
    return {"added": [dict(r) for need, r in news.items() if need not in olds],
            "removed": [dict(r) for need, r in olds.items() if need not in news],
            "changed": changed}


def _decision_diff(old: list[dict], new: list[dict]) -> dict:
    olds = {d.get("id"): d for d in old}
    news = {d.get("id"): d for d in new}
    changed = [{"id": i, "topic": d.get("topic", ""), "old": olds[i].get("recommended", ""), "new": d.get("recommended", "")}
               for i, d in news.items() if i in olds and olds[i].get("recommended") != d.get("recommended")]
    return {"changed": changed,
            "added": [dict(d) for i, d in news.items() if i not in olds],
            "removed": [dict(d) for i, d in olds.items() if i not in news]}


def _short(need: str) -> str:
    return need.split("（")[0]


def _reconfirm(d: Diff) -> list[dict]:
    """需重新确认：新冒出的高影响未答问题、问法变了的已答问题、原话改后不见了的功能点、改了或新增的 IT 决策、
    新落到需申请 / 需新建 / 时效不符的数据项。"""
    items = []
    for q in d.questions["new"]:
        if q.get("impact") == "高" and not (q.get("answer") or "").strip():
            items.append({"who": "业务", "kind": "新问题", "text": f"新增高影响问题：{q.get('question', '')}（不答按默认：{q.get('default') or '待定'}）"})
    for q in d.questions["reworded"]:
        # 只有问法本身变了，原答复才可能不再适用；只是影响档或默认假设变了，已答的口径照用
        if q.get("answer") and q.get("old") != q.get("new"):
            items.append({"who": "业务", "kind": "口径", "text": f"问题范围变了，原答复是否仍适用：{q['new']}（原答复：{q['answer']}）"})
    # 原话没改、或一版规则初稿一版模型结果时，功能点增删只是抽取 / 模型改写的说法差别，不拿去问业务
    for feature in d.features["removed"] if d.text_changed and not d.mixed else []:
        items.append({"who": "业务", "kind": "功能点", "text": f"功能点不见了，确认业务确实不要：{feature}"})
    for x in d.decisions["changed"]:
        items.append({"who": "IT", "kind": "决策", "text": f"决策 {x['id']} {x['topic']}：建议由「{x['old']}」改为「{x['new']}」，需重新拍板"})
    for x in d.decisions["added"]:
        items.append({"who": "IT", "kind": "决策", "text": f"新增决策 {x.get('id', '')} {x.get('topic', '')}：建议「{x.get('recommended', '')}」，需拍板"})
    groups: dict[str, list[str]] = {}
    for row in d.data_map["added"]:
        if row.get("status") in ATTENTION:
            groups.setdefault(row["status"], []).append(_short(row.get("need", "")))
    for c in d.data_map["changed"]:
        status = c["new"].get("status", "")
        if status in ATTENTION and status != c["old"].get("status", ""):
            groups.setdefault(status, []).append(_short(c["need"]))
    for status in ATTENTION:
        if groups.get(status):
            names = list(dict.fromkeys(groups[status]))
            items.append({"who": "数据", "kind": "数据地图",
                          "text": f"数据项「{status}」{len(names)} 项（{'、'.join(names)}）：{ATTENTION[status]}"})
    return items


def _pm(added: int, removed: int) -> str:
    if added and removed:
        return f"+{added}/−{removed}"
    return f"+{added}" if added else f"−{removed}"


def _estimate_tokens(e: dict) -> list[str]:
    out = []
    model, rule, history = e.get("model") or {}, e.get("rule") or {}, e.get("history") or {}
    if model.get("changed"):
        rows = model.get("rows") or []
        moved = [r for r in rows if r.get("delta")]
        if moved:
            r = moved[0]
            out.append(f"模型估时（{TRACK_SHORT[r['track']]}）{fmt_num(r['old'])}→{fmt_num(r['new'])} 小时")
        elif all(r.get("old") is None for r in rows):
            out.append("新出模型估时")
        elif all(r.get("new") is None for r in rows):
            out.append("模型估时本版无结果")
        else:
            out.append("模型估时区间有变")
    if rule.get("changed"):
        rows = rule.get("rows") or []
        moved = [r for r in rows if r.get("delta")]
        if moved:
            r = moved[0]
            out.append(f"规则参考（{TRACK_SHORT[r['track']]}）{fmt_num(r['old'])}→{fmt_num(r['new'])} 小时")
        elif all(r.get("old") is None for r in rows):
            out.append("新出规则参考")
        elif all(r.get("new") is None for r in rows):
            out.append("规则参考本版无")
    if history.get("old") != history.get("new"):
        out.append(f"历史参考 {history.get('old', 0)}→{history.get('new', 0)} 条")
    return out


def _summary(d: Diff) -> str:
    parts = ["原话已改"] if d.text_changed else []
    f, q, m, dec = d.features, d.questions, d.data_map, d.decisions
    if f["added"] or f["removed"]:
        parts.append("功能点 " + _pm(len(f["added"]), len(f["removed"])))
    if f["reworded"]:
        parts.append(f"功能点改写 {len(f['reworded'])}")
    if d.fields:
        parts.append(f"要素变化 {len(d.fields)}")
    if q["new"] or q["dropped"]:
        parts.append("待确认 " + _pm(len(q["new"]), len(q["dropped"])))
    if q["answered"]:
        parts.append(f"已答 +{len(q['answered'])}")
    if q["changed"]:
        parts.append(f"改答 {len(q['changed'])}")
    parts += _estimate_tokens(d.estimate)
    if m["added"] or m["removed"]:
        parts.append("数据地图 " + _pm(len(m["added"]), len(m["removed"])))
    if m["changed"]:
        parts.append(f"数据项变化 {len(m['changed'])}")
    n_dec = len(dec["changed"]) + len(dec["added"]) + len(dec["removed"])
    if n_dec:
        parts.append(f"决策变化 {n_dec}")
    if d.reconfirm:
        parts.append(f"需重新确认 {len(d.reconfirm)} 项")
    head = f"相对 v{d.v_old}" if d.v_old else "相对上一版"
    return f"{head}：" + (" · ".join(parts) if parts else "内容无变化")


def diff(old: dict, new: dict, v_old: int = 0, v_new: int = 0) -> Diff:
    """两版快照 → 结构化差异（功能点 / 要素 / 待确认 / 工时 / 数据地图 / 决策 / 需重新确认）+ 一句话概要。"""
    old, new = old or {}, new or {}
    d = Diff(v_old=int(v_old or 0), v_new=int(v_new or 0))
    sig_old, sig_new = old.get("text_sig", ""), new.get("text_sig", "")
    d.text_changed = bool(sig_old and sig_new and sig_old != sig_new)
    d.mixed = bool(kind(old) and kind(new) and kind(old) != kind(new))
    d.features = _feature_diff(list(old.get("features") or []), list(new.get("features") or []))
    d.fields = _field_diff(old, new)
    d.questions = _question_diff(list(old.get("questions") or []), list(new.get("questions") or []))
    d.estimate = _estimate_diff(old.get("estimate") or {}, new.get("estimate") or {}, d.v_old, d.v_new)
    d.data_map = _data_diff(list(old.get("data_map") or []), list(new.get("data_map") or []))
    d.decisions = _decision_diff(list(old.get("decisions") or []), list(new.get("decisions") or []))
    d.reconfirm = _reconfirm(d)
    d.summary = _summary(d)
    return d


def same(old: dict, new: dict) -> bool:
    """两版看不出差别（只是顺序变了也算一样）：存版本时据此去重，页面据此判断屏上是不是最新一版。"""
    return not diff(old, new).changed


def base_index(found: list[dict], i: int | None = None) -> int:
    """第 i 版（默认最新一版，下标从 0 起）的「本次变更」拿哪一版比：往前找最近一份同类结果
    （同为规则初稿或同为含模型结果），找不到就用上一版。found 为 Ledger.versions_of() 的结果，至少两版。"""
    i = len(found) - 1 if i is None else i
    want = kind(found[i].get("snapshot"))
    for j in range(i - 1, -1, -1):
        if kind(found[j].get("snapshot")) == want:
            return j
    return i - 1


def base_note(found: list[dict], j: int, i: int | None = None) -> str:
    """base_index 跳过了中间版本时说一句为什么；没跳过返回空。"""
    i = len(found) - 1 if i is None else i
    skipped = found[j + 1:i]
    if not skipped:
        return ""
    names = "、".join(f"v{v['version']}（{v.get('label') or '—'}）" for v in skipped)
    if kind(found[i].get("snapshot")) == "模型":
        return f"{names}是模型补全前的规则初稿，这里直接和上一份同样含模型结果的 v{found[j]['version']} 比。"
    return f"{names}含模型结果，和本版规则初稿不同类，这里和上一份规则初稿 v{found[j]['version']} 比。"


def reworded_note(x: dict) -> str:
    """问题「说法变了」的补充：影响档或默认假设也变了时写出来。"""
    bits = []
    if x.get("old_impact") and x.get("old_impact") != x.get("impact"):
        bits.append(f"影响 {x['old_impact']}→{x.get('impact', '')}")
    if x.get("old_default", "") != x.get("default", ""):
        bits.append(f"默认改为：{x.get('default') or '待定'}")
    return f"（{'；'.join(bits)}）" if bits else ""


# ---------------------------------------------------------------- 变更说明
def _vtag(v) -> str:
    if isinstance(v, dict):
        ts = str(v.get("ts") or "")[:16].replace("T", " ")
        bits = " · ".join(x for x in (str(v.get("label") or ""), ts) if x)
        return f"v{v.get('version', '?')}" + (f"（{bits}）" if bits else "")
    return f"v{v}"


def _cell(value) -> str:
    if isinstance(value, list):
        value = "、".join(str(x) for x in value) or "—"
    text = str(value if value not in (None, "") else "—")
    return text.replace("|", "\\|").replace("\n", " ")


def change_text(delta, pct) -> str:
    """工时一行的变化：两边都有数才写增减，缺一边写「不比较」。"""
    if delta is None:
        return "不比较"
    if not delta:
        return "不变"
    return f"{delta:+g}" + (f"（{pct:+.1f}%）" if pct is not None else "")


def field_change(x: dict) -> str:
    """要素一行的变化说明：列表字段写增删，单值字段只标改了 / 新填（新旧值在表里两列）。"""
    if x.get("added") or x.get("removed"):
        bits = [f"+{a}" for a in x.get("added") or []] + [f"−{r}" for r in x.get("removed") or []]
        return " ".join(bits)
    return "改了" if x.get("old") else "新填"


def diff_markdown(d: Diff, title: str, v_old, v_new) -> str:
    """变更说明（给业务 / 开发）：先列需重新确认，再分节写增删改；v_old / v_new 可传版本号或 versions_of() 的一项。"""
    lines = [f"# 需求变更说明：{title or '未命名需求'}", "",
             f"- 版本：{_vtag(v_old)} → {_vtag(v_new)}", f"- 概要：{d.summary}", ""]
    if d.mixed:
        lines += [f"> {MIXED_NOTE}", ""]
    if not d.changed:
        lines += ["两版分析结果一致，没有需要重新确认的事项。", ""]
    if d.reconfirm:
        lines += [f"## 需重新确认（{len(d.reconfirm)} 项）", ""]
        lines += [f"- [ ] 【{r['who']}】{r['text']}" for r in d.reconfirm]
        lines.append("")
    if d.text_changed:
        lines += ["## 原话", "", "原话有改动（版本里只存指纹，不存原文；原文以需求方最新发来的为准）。", ""]
    f = d.features
    if any(f.values()):
        lines += ["## 功能点", ""]
        lines += [f"- 新增：{x}" for x in f["added"]]
        lines += [f"- 删除：{x}" for x in f["removed"]]
        lines += [f"- 改写：{x['old']} → {x['new']}" for x in f["reworded"]]
        lines.append("")
    if d.fields:
        lines += ["## 要素", "", f"| 要素 | v{d.v_old} | v{d.v_new} | 变化 |", "|---|---|---|---|"]
        lines += [f"| {x['label']} | {_cell(x['old'])} | {_cell(x['new'])} | {_cell(field_change(x))} |" for x in d.fields]
        lines.append("")
    q = d.questions
    if any(q.values()):
        lines += ["## 待确认清单", ""]
        lines += [f"- 新增（影响{x.get('impact', '')}）：{x.get('question', '')}" + (f" —— 已答：{x['answer']}" if x.get("answer") else "")
                  for x in q["new"]]
        lines += [f"- 不再需要：{x.get('question', '')}" + (f"（原答复：{x['answer']}）" if x.get("answer") else "") for x in q["dropped"]]
        lines += [f"- 新答复：{x['question']} —— {x['answer']}" for x in q["answered"]]
        lines += [f"- 改了答复：{x['question']} —— {x['old']} → {x['new'] or '（撤回，按默认：' + (x.get('default') or '待定') + '）'}"
                  for x in q["changed"]]
        lines += [f"- 说法变了（以新问法为准）：{x['new']}{reworded_note(x)}" for x in q["reworded"]]
        lines.append("")
    e = d.estimate or {}
    model, rule, history = e.get("model") or {}, e.get("rule") or {}, e.get("history") or {}
    if e.get("changed"):
        lines += ["## 工时（小时；模型估时与规则参考分开比较，不混算）", "",
                  f"| 来源 | 口径 | v{d.v_old} | v{d.v_new} | 变化 |", "|---|---|---|---|---|"]
        for r in model.get("rows") or []:
            old = fmt_num(r["old"]) + (f"（{r['old_range']}）" if r.get("old_range") else "")
            new = fmt_num(r["new"]) + (f"（{r['new_range']}）" if r.get("new_range") else "")
            lines.append(f"| 模型估时 | {r['label']} | {old} | {new} | {change_text(r['delta'], r['pct'])} |")
        for r in rule.get("rows") or []:
            lines.append(f"| 规则参考 | {r['label']} | {fmt_num(r['old'])} | {fmt_num(r['new'])} | {change_text(r['delta'], r['pct'])} |")
        if history.get("old") != history.get("new"):
            lines.append(f"| 历史参考 | 相关记录 | {history.get('old', 0)} 条 | {history.get('new', 0)} 条 | 只并列，不推算工时 |")
        lines.append("")
        lines += [f"> {note}" for note in (model.get("note"), rule.get("note")) if note]
        if model.get("note") or rule.get("note"):
            lines.append("")
    m = d.data_map
    if any(m.values()):
        lines += ["## 数据地图", ""]
        lines += [f"- 新增：{x['need']} ← {x.get('system', '')} · {x.get('interface', '')}（{x.get('freshness', '')}，{x.get('status', '')}）"
                  for x in m["added"]]
        lines += [f"- 删除：{x['need']}（原来 {x.get('system', '')}，{x.get('status', '')}）" for x in m["removed"]]
        for x in m["changed"]:
            bits = "；".join(f"{DATA_LABELS[k]} {v[0] or '—'} → {v[1] or '—'}" for k, v in x["changes"].items())
            lines.append(f"- 变化：{x['need']}：{bits}")
        lines.append("")
    dec = d.decisions
    if any(dec.values()):
        lines += ["## IT 决策", ""]
        lines += [f"- 变化：{x['id']} {x['topic']}：{x['old']} → {x['new']}" for x in dec["changed"]]
        lines += [f"- 新增：{x.get('id', '')} {x.get('topic', '')}：建议 {x.get('recommended', '')}" for x in dec["added"]]
        lines += [f"- 删除：{x.get('id', '')} {x.get('topic', '')}（原建议 {x.get('recommended', '')}）" for x in dec["removed"]]
        lines.append("")
    lines += ["---", "需知自动生成：只对比两版分析结果，不调模型；模型估时与规则参考分开比较、不混算；"
              "工时为投入小时（8 小时 = 1 人天），不是交付承诺。"]
    return "\n".join(lines)

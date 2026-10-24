"""需知 · 需求版本对比页面件：开工按钮下的「需求变更」开关、结果顶上的「本次变更」、一本账里的「版本记录」。
差异全部由 xuzhi/versions.py 程序算出、不调模型；徽标用 Streamlit 自带的彩色 badge：新增绿、删除红、变化琥珀。"""
from __future__ import annotations

import json
from collections.abc import Iterable

import pandas as pd
import streamlit as st

from xuzhi import textutil, versions
from xuzhi.ledger import Ledger

ADD, DEL, CHG = ":green-badge", ":red-badge", ":orange-badge"
WHO = {"业务": ":blue-badge", "IT": ":violet-badge", "数据": ":gray-badge"}


def _md(text) -> str:
    # 原话、问题、答复都按字面显示，不让其中的 * _ | 等被当成 Markdown
    return textutil.markdown_literal(str(text if text is not None else ""))


def _section(title: str, rows: Iterable[str]) -> None:
    rows = list(rows)
    if rows:
        st.markdown(f"**{title}**\n\n" + "\n".join(f"- {r}" for r in rows))


def _value(value) -> str:
    if isinstance(value, list):
        return "、".join(str(x) for x in value) or "—"
    return str(value) if value not in (None, "") else "—"


def _ts(ts: str) -> str:
    return str(ts or "")[5:16].replace("T", " ")


def _versions(ledger: Ledger, req_id: int) -> list[dict]:
    if not req_id:
        return []
    try:
        return ledger.versions_of(int(req_id))
    except Exception:   # 版本记录只是附加信息，读不到不影响主流程
        return []


# ---------------------------------------------------------------- 结构化差异
def render_diff(d: versions.Diff) -> None:
    """先列需重新确认，再分节写功能点 / 要素 / 待确认 / 工时 / 数据地图 / IT 决策；只画有变化的节。"""
    if not d.changed:
        st.caption("两版分析结果一致。")
        return
    if d.mixed:
        st.caption(versions.MIXED_NOTE)
    old_col, new_col = f"旧（v{d.v_old}）", f"新（v{d.v_new}）"
    _section(f"需重新确认（{len(d.reconfirm)} 项）",
             (f"{WHO.get(r['who'], ':gray-badge')}[{r['who']}] {_md(r['text'])}" for r in d.reconfirm))
    if d.text_changed:
        st.caption("原话有改动；版本里只存原话指纹，不存原文。")
    f = d.features
    _section("功能点", [f"{ADD}[新增] {_md(x)}" for x in f["added"]]
             + [f"{DEL}[删除] {_md(x)}" for x in f["removed"]]
             + [f"{CHG}[改写] {_md(x['old'])} → {_md(x['new'])}" for x in f["reworded"]])
    if d.fields:
        st.markdown("**要素**")
        st.table(pd.DataFrame([{"要素": x["label"], old_col: _md(_value(x["old"])), new_col: _md(_value(x["new"])),
                                "变化": _md(versions.field_change(x))} for x in d.fields]), hide_index=True)
    q = d.questions
    _section("待确认清单",
             [f"{ADD}[新增·{x.get('impact', '')}] {_md(x.get('question', ''))}"
              + (f" —— 已答：{_md(x['answer'])}" if x.get("answer") else "") for x in q["new"]]
             + [f"{DEL}[不再需要] {_md(x.get('question', ''))}" + (f"（原答复：{_md(x['answer'])}）" if x.get("answer") else "")
                for x in q["dropped"]]
             + [f"{ADD}[新答复] {_md(x['question'])} → **{_md(x['answer'])}**" for x in q["answered"]]
             + [f"{CHG}[改答] {_md(x['question'])}：{_md(x['old'])} → **{_md(x['new'] or '撤回，按默认')}**" for x in q["changed"]]
             + [f"{CHG}[说法变了] {_md(x['new'])}{_md(versions.reworded_note(x))}" for x in q["reworded"]])
    e = d.estimate or {}
    if e.get("changed"):
        model, rule, history = e.get("model") or {}, e.get("rule") or {}, e.get("history") or {}
        rows = []
        for r in model.get("rows") or []:
            rows.append({"来源": "模型估时", "口径": r["label"],
                         old_col: versions.fmt_num(r["old"]) + (f"（{r['old_range']}）" if r.get("old_range") else ""),
                         new_col: versions.fmt_num(r["new"]) + (f"（{r['new_range']}）" if r.get("new_range") else ""),
                         "变化": versions.change_text(r["delta"], r["pct"])})
        for r in rule.get("rows") or []:
            rows.append({"来源": "规则参考", "口径": r["label"], old_col: versions.fmt_num(r["old"]),
                         new_col: versions.fmt_num(r["new"]), "变化": versions.change_text(r["delta"], r["pct"])})
        if history.get("old") != history.get("new"):
            rows.append({"来源": "历史参考", "口径": "相关记录", old_col: f"{history.get('old', 0)} 条",
                         new_col: f"{history.get('new', 0)} 条", "变化": "只并列，不推算工时"})
        st.markdown("**工时（小时）**")
        if rows:
            st.table(pd.DataFrame(rows), hide_index=True)
        for note in (model.get("note"), rule.get("note")):
            if note:
                st.caption(note)
        st.caption("模型估时只和模型估时比，规则参考只和规则参考比，两路数字不互相加减；8 小时 = 1 人天，是投入工时不是工期。")
    m = d.data_map
    _section("数据地图",
             [f"{ADD}[新增] {_md(x['need'])} ← {_md(x.get('system', ''))} · {_md(x.get('interface', ''))}"
              f"（{_md(x.get('freshness', ''))}，{_md(x.get('status', ''))}）" for x in m["added"]]
             + [f"{DEL}[删除] {_md(x['need'])}（原来 {_md(x.get('system', ''))}，{_md(x.get('status', ''))}）" for x in m["removed"]]
             + [f"{CHG}[变化] {_md(x['need'])}：" + "；".join(f"{versions.DATA_LABELS[k]} {_md(v[0] or '—')} → **{_md(v[1] or '—')}**"
                                                          for k, v in x["changes"].items()) for x in m["changed"]])
    dec = d.decisions
    _section("IT 决策",
             [f"{CHG}[变化] {_md(x['id'])} {_md(x['topic'])}：{_md(x['old'])} → **{_md(x['new'])}**" for x in dec["changed"]]
             + [f"{ADD}[新增] {_md(x.get('id', ''))} {_md(x.get('topic', ''))}：建议 {_md(x.get('recommended', ''))}" for x in dec["added"]]
             + [f"{DEL}[删除] {_md(x.get('id', ''))} {_md(x.get('topic', ''))}（原建议 {_md(x.get('recommended', ''))}）"
                for x in dec["removed"]])


def _note_md(d: versions.Diff, title: str, old: dict, new: dict) -> tuple[str, str]:
    name = f"变更说明_{title or '需求'}_v{old['version']}-v{new['version']}.md"
    return versions.diff_markdown(d, title, old, new), name


# ---------------------------------------------------------------- 结果顶上：本次变更
def _latest_on_screen(found: list[dict], analysis) -> bool:
    if len(found) < 2:
        return False
    if analysis is None:
        return True
    try:
        on_screen = json.loads(json.dumps(versions.snapshot(analysis), ensure_ascii=False))
        return versions.same(found[-1]["snapshot"], on_screen)
    except Exception:
        return False


def render_change_banner(ledger: Ledger, req_id: int, analysis=None) -> None:
    """屏上这份结果就是本条最新一版、且前面还有版本时，在结果顶上一句话说清这次改了什么；展开看明细、下载说明。
    和上一份同类结果比（versions.base_index）：配了模型时，跳过模型补全前的规则初稿，不把说法差别当变更。"""
    lost = st.session_state.pop("_xz_change_lost", None)
    same = st.session_state.pop("_xz_change_same", None)
    found = _versions(ledger, req_id)
    # 模型还在跑或刚失败：屏上不是存过的那一版，先不说「本次变更」，免得和页面对不上
    show = _latest_on_screen(found, analysis)
    if not (lost or same or show):
        return
    # 带键的容器：需求变更开工后页面滚到这里（app.py 的 scroll_to_results），一眼看到改了什么
    with st.container(key="xz_change_banner"):
        if lost:
            st.warning(f"#{lost} 已不在一本账里（可能重置过演示数据），这次按新需求受理为 #{req_id}。")
        if same:
            st.info(f"原话和分析结果与 v{same} 相同，没有另存版本。")
        if not show:
            return
        j = versions.base_index(found)
        old, new = found[j], found[-1]
        d = versions.diff(old["snapshot"], new["snapshot"], old["version"], new["version"])
        with st.container(border=True):
            st.markdown(f"{CHG}[:material/difference: 本次变更] **v{new['version']} · {_md(new['label'] or '更新')}**"
                        f"　{_md(d.summary)}")
            note = versions.base_note(found, j)
            if note:
                st.caption(note + "逐版对比见「一本账 → 版本记录」。")
            with st.expander(f"变更明细（v{old['version']} → v{new['version']}）：需重新确认、增删改、工时与架构影响"):
                render_diff(d)
                body, name = _note_md(d, new["snapshot"].get("title") or "", old, new)
                st.download_button("下载本次变更说明 .md", body, file_name=name, mime="text/markdown", on_click="ignore",
                                   icon=":material/download:", key=f"chg_note_{req_id}_{old['version']}_{new['version']}")


# ---------------------------------------------------------------- 开工：需求变更
def change_toggle() -> int:
    """屏上已有一条分析时，开工按钮下给个开关：把这次开工记成它的新版本。默认关；返回要变更的编号，0 = 新需求。"""
    rid = int(st.session_state.get("req_id", 0) or 0)
    if not rid or st.session_state.get("analysis") is None:
        return 0
    # 键里带编号、轮次与所选样例：换了需求、变更落账或改选样例后开关复位，避免把另一条需求误记成变更
    key = f"as_change_{rid}_{st.session_state.get('as_change_rev', 0)}_{st.session_state.get('sample', '')}"
    on = st.toggle(f"作为当前需求 #{rid} 的新版本（需求变更）", key=key,
                   help="原话改了时打开：沿用这个编号和已答口径重新分析，存为新版本并标出改了什么、要重新确认什么；关着就作为一条新需求受理。")
    return rid if on else 0


def carried_answers(analysis, typed: dict | None) -> dict[str, str]:
    """需求变更时带过去的业务答复：以答复框里的为准（含还没点重算的），框里没有就用本版已用的答复。
    先读答复框控件（键 ans_<题号>）：开工在问清页之前执行，同一次点击里刚填的字还没写回 typed。
    沿用记忆且没改过的不带——新分析会按记忆重新沿用并标 🧠。新清单里没有的题，analyze 自然不用。"""
    typed = typed or {}
    out: dict[str, str] = {}
    for q in getattr(analysis, "questions", None) or []:
        live = st.session_state.get(f"ans_{q.id}")
        value = str((live if isinstance(live, str) else typed.get(q.id, q.answer)) or "").strip()
        if not value or (getattr(q, "recalled", "") and value == (q.answer or "").strip()):
            continue
        out[q.id] = value
    return out


def apply_change(ledger: Ledger, req_id: int, analysis) -> None:
    """需求变更落账：同一编号更新、记「需求变更」并存一版；答复框换成新清单里仍有的题。原编号已不在账上就按新需求受理。"""
    before = len(_versions(ledger, req_id))
    # 沿用记忆的口径不放进答复框的已填项，由新分析重新预填并标 🧠
    st.session_state.answers = {q.id: q.answer for q in analysis.questions if q.answer.strip() and not getattr(q, "recalled", "")}
    try:
        n = ledger.record_change(req_id, analysis)
    except ValueError:
        st.session_state.req_id = ledger.log_analysis(analysis)
        st.session_state["_xz_change_lost"] = req_id
        return
    st.session_state.req_id = req_id
    st.session_state["as_change_rev"] = int(st.session_state.get("as_change_rev", 0) or 0) + 1
    if n == before:
        st.session_state["_xz_change_same"] = n


# ---------------------------------------------------------------- 一本账：版本记录
def _row(v: dict) -> dict:
    s = v.get("snapshot") or {}
    e = s.get("estimate") or {}
    model, rule = e.get("model"), e.get("rule")
    qs = s.get("questions") or []
    return {
        "版本": f"v{v['version']}", "时间": _ts(v.get("ts", "")), "动作": v.get("label") or "—",
        # 规则初稿还是含模型结果：配了模型时两类交替出现，「本次变更」只拿同类比
        "结果": {"规则": "规则初稿", "模型": "含模型结果"}.get(versions.kind(s), "—"), "标题": s.get("title") or "—",
        "功能点": len(s.get("features") or []), "待确认（已答）": f"{len(qs)}（{sum(1 for q in qs if q.get('answer'))}）",
        "模型估时 以前/现在（小时）": (f"{versions.fmt_num(model['before']['mid'])} / {versions.fmt_num(model['after']['mid'])}"
                                if model else "—"),
        "规则参考 以前/现在（小时）": (f"{versions.fmt_num(rule.get('before'))} / {versions.fmt_num(rule.get('after'))}"
                                if rule else "—"),
    }


def render_version_history(ledger: Ledger, req_id: int) -> None:
    """一本账里选中的需求：版本记录 + 任选两版对比 + 下载变更说明。Jira 工单有自己的「Jira 重新评估」，不记版本。"""
    row = ledger.get(int(req_id)) if req_id else None
    if not row or row.get("source") == "Jira":
        return
    found = _versions(ledger, req_id)
    with st.expander(f"版本记录（{len(found)} 版）"):
        if not found:
            st.caption("这条早于版本功能，还没有版本；之后答复重算、模型增强或需求变更会从这里开始记。")
            return
        st.dataframe(pd.DataFrame([_row(v) for v in found]), hide_index=True, width="stretch")
        if len(found) < 2:
            st.caption("目前只有一版：业务答复重算、模型增强或需求变更后就能对比。")
            return
        options = [v["version"] for v in found]
        names = {v["version"]: f"v{v['version']} · {v.get('label') or '—'} · {_ts(v.get('ts', ''))}" for v in found}
        left, right = st.columns(2)
        # 键里带版本数：来了新版本时两个选择框回到「最近两版」
        a = left.selectbox("从版本", options, index=len(options) - 2, format_func=names.get, key=f"ver_from_{req_id}_{len(found)}")
        b = right.selectbox("到版本", options, index=len(options) - 1, format_func=names.get, key=f"ver_to_{req_id}_{len(found)}")
        if a == b:
            st.caption("选的是同一版，没有差异。")
            return
        old, new = found[a - 1], found[b - 1]
        d = versions.diff(old["snapshot"], new["snapshot"], a, b)
        st.markdown(f"**{_md(d.summary)}**")
        render_diff(d)
        body, name = _note_md(d, new["snapshot"].get("title") or row.get("title") or "", old, new)
        st.download_button("下载变更说明 .md", body, file_name=name, mime="text/markdown", on_click="ignore",
                           icon=":material/download:", key=f"ver_note_{req_id}_{a}_{b}")

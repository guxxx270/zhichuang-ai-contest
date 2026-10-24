"""需求一本账（台账）：SQLite 记录每一次分析（卡片 / 清单 / 估算 / 决策）、业务答复、状态流转、对账与反馈。
既是审计留痕，也是估算飞轮的数据：对账回写实际人天 → 追加到历史需求库 → 下次估量自动校准；看板按部门 / 类型 / 状态汇总。
每条需求另存版本快照（versions 表）：受理、答复重算、模型增强、需求变更各一版，供版本对比（xuzhi/versions.py）。"""
from __future__ import annotations

import dataclasses
import json
import re

import math
import sqlite3
from dataclasses import asdict, is_dataclass
from datetime import datetime

from . import config, versions
from .jira import DONE_STATUSES, HOURS_PER_DAY, JiraIssue

MINUTES_SAVED = {"问清": 60, "写单": 90, "估量": 30, "定架": 60, "出样": 120}   # 与人工相比的估计节省，方案文档里需校准

STATUSES = ["受理", "已确认", "开发中", "已交付", "已对账"]
JIRA_SOURCE = "Jira"
JIRA_DEPT = "Jira 工单"
_NOT_JIRA = f"COALESCE(source,'') != '{JIRA_SOURCE}'"


def _jira_status(issue: JiraIssue) -> str:
    """Jira 状态 → 一本账状态。工单已写好，待办即「已确认」；已完成的先记「已交付」，有耗费时间再对账。"""
    s = (issue.status or "").strip().lower()
    if issue.done or s in DONE_STATUSES:
        return "已交付"
    if re.search(r"进行|处理中|开发|测试|验收|in progress|review|qa", s):
        return "开发中"
    return "已确认"


def _dept_of(requester: str) -> str:
    r = (requester or "").strip()
    if not r:
        return "未注明"
    return r.split("·")[0].split("/")[0].strip() or "未注明"


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def estimate_snapshot(estimate) -> dict:
    """保存模型与独立参考；模型失败仅清模型工时，保留各来源的参考快照。"""
    snapshot = asdict(estimate) if is_dataclass(estimate) else dict(vars(estimate))
    ready = snapshot.get("status") == "ready"
    snapshot.update({
        "method": snapshot.get("method", "unavailable"),
        "status": snapshot.get("status", "unavailable"),
        "basis": snapshot.get("basis", "requirements_only"),
        "model": snapshot.get("model", ""), "code_files": list(snapshot.get("code_files") or []),
        "summary": snapshot.get("summary", ""), "error": snapshot.get("error", ""),
        "references": dict(snapshot.get("references") or {}),
        "assumptions": list(snapshot.get("assumptions") or []), "hours_per_day": 8,
    })
    if not ready:
        for key in ("mid", "low", "high", "ai_mid", "ai_low", "ai_high", "saving_pct", "base_days", "history_days"):
            snapshot[key] = None
        snapshot.update(tasks=[], phases={}, ai_phases={}, who={})

    def track(label, low, mid, high):
        return {"label": label, "unit": "小时", "low": low, "mid": mid, "high": high}

    def hours(value):
        number = _number(value)
        return None if number is None else number * 8

    snapshot["before"] = track("以前", hours(snapshot.get("low")), hours(snapshot.get("mid")), hours(snapshot.get("high")))
    snapshot["after"] = track("现在", hours(snapshot.get("ai_low")), hours(snapshot.get("ai_mid")), hours(snapshot.get("ai_high")))
    tasks = []
    for item in snapshot.get("tasks") or []:
        task = asdict(item) if is_dataclass(item) else (dict(item) if isinstance(item, dict) else dict(vars(item)))
        task["before_reason"] = task.get("before_reason", "")
        task["after_reason"] = task.get("after_reason", "")
        task["evidence"] = list(task.get("evidence") or [])
        task["before"] = track("以前", task.get("trad_low_hours"), hours(task.get("trad_days")), task.get("trad_high_hours"))
        task["after"] = track("现在", task.get("ai_low_hours"), hours(task.get("ai_days")), task.get("ai_high_hours"))
        tasks.append(task)
    snapshot["tasks"] = tasks
    return snapshot


class Ledger:
    def __init__(self, path=None) -> None:
        self.path = str(path or config.LEDGER_DB)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS requirements(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, title TEXT, source TEXT, req_type TEXT, requester TEXT,
            engine TEXT, n_questions INTEGER, n_answered INTEGER, mid_days REAL, confidence TEXT, redacted INTEGER, payload TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, req_id INTEGER, action TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, req_id INTEGER, skill TEXT, score INTEGER, note TEXT);
        CREATE TABLE IF NOT EXISTS versions(id INTEGER PRIMARY KEY AUTOINCREMENT, req_id INTEGER, ts TEXT, label TEXT, snapshot TEXT);
        CREATE INDEX IF NOT EXISTS idx_versions_req ON versions(req_id);
        """)
        # 逐列补齐（老库无该列时才加）
        for col, typ in (("ai_days", "REAL"), ("dept", "TEXT"), ("status", "TEXT"), ("actual_days", "REAL"),
                         ("ai_assisted", "INTEGER"), ("ai_share", "REAL"), ("closed_ts", "TEXT"), ("close_note", "TEXT"),
                         ("seconds", "REAL"), ("n_high_open", "INTEGER"),
                         ("ext_key", "TEXT"), ("coding_trad", "REAL"), ("coding_ai", "REAL")):   # Jira 单号、编码阶段人工 / AI
            try:
                self.conn.execute(f"ALTER TABLE requirements ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass   # 已有该列
        self.conn.commit()

    # ---------- 写 ----------
    def log_analysis(self, a) -> int:
        est = estimate_snapshot(a.estimate)
        cur = self.conn.execute(
            "INSERT INTO requirements(ts,title,source,req_type,requester,engine,n_questions,n_answered,mid_days,confidence,redacted,ai_days,"
            "dept,status,seconds,n_high_open,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), a.card.title, a.card.source, a.card.req_type, a.card.requester, a.card.engine,
             len(a.questions), sum(1 for q in a.questions if q.answer.strip()), est.get("mid"), a.estimate.confidence, a.redacted, est.get("ai_mid"),
             _dept_of(a.card.requester), "受理", float(getattr(a, "seconds", 0.0) or 0.0), int(getattr(a.estimate, "unanswered_high", 0) or 0),
             json.dumps({"card": json.loads(a.card.to_json()), "questions": [q.__dict__ for q in a.questions],
                         "estimate": est,
                         "decisions": [d.recommended for d in a.architecture.decisions],
                         "data_map": [r.__dict__ for r in getattr(a.architecture, "data_map", [])]}, ensure_ascii=False)))
        self.conn.commit()
        rid = int(cur.lastrowid)
        self.add_version(rid, a, "受理")
        return rid

    def log_jira(self, a, origin: str = "粘贴") -> tuple[int, bool]:
        """记一条 Jira 评估（xuzhi.pipeline.assess.Assessment）。同一单号再评只更新数字、记「Jira 重新评估」，不重复建账；
        Jira 里已完成且有耗费时间的，直接按实际耗时对账。返回 (编号, 是否新建)。"""
        it = a.issue
        key = it.key or f"摘要:{it.summary[:40]}"
        title = f"{it.key} {it.summary}".strip() or "未命名工单"
        payload = json.dumps({
            "jira": dataclasses.asdict(it), "kind": a.kind, "level": a.level, "dims": a.dims, "risks": a.risks,
            "coding": [a.coding_trad, a.coding_ai], "full_ai": a.full_ai, "jira_days": a.jira_days, "deviation": a.deviation,
            "tasks": [{"name": t.name, "phase": t.phase, "who": t.who, "trad": t.trad_days, "ai": t.ai_days} for t in a.tasks],
        }, ensure_ascii=False)
        n_hi = sum(1 for lv, _ in a.risks if lv == "高")
        detail = (f"传统 {a.mid} 人天 · AI 编码 {a.coding_ai}（编码阶段 {a.coding_trad}）· 全流程 AI {a.full_ai} · "
                  f"复杂度 {a.level} {a.total} · 来源 Jira {origin}")
        found = self.conn.execute("SELECT id, status FROM requirements WHERE ext_key=?", (key,)).fetchone()
        if found:
            rid, cur_status, created = int(found[0]), found[1] or "受理", False
            self.conn.execute(
                "UPDATE requirements SET title=?, req_type=?, mid_days=?, ai_days=?, coding_trad=?, coding_ai=?, confidence=?, "
                "n_high_open=?, payload=? WHERE id=?",
                (title, a.kind, a.mid, a.full_ai, a.coding_trad, a.coding_ai, a.confidence, n_hi, payload, rid))
            self.event(rid, "Jira 重新评估", detail)
        else:
            cur = self.conn.execute(
                "INSERT INTO requirements(ts,title,source,req_type,requester,engine,n_questions,n_answered,mid_days,confidence,redacted,ai_days,"
                "dept,status,seconds,n_high_open,payload,ext_key,coding_trad,coding_ai) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), title, JIRA_SOURCE, a.kind, "", "规则", 0, 0, a.mid, a.confidence, 0,
                 a.full_ai, JIRA_DEPT, "已确认", 0.0, n_hi, payload, key, a.coding_trad, a.coding_ai))
            rid, cur_status, created = int(cur.lastrowid), "已确认", True
            self.event(rid, "Jira 评估", detail)
        self.conn.commit()
        want = _jira_status(it)
        if cur_status != "已对账" and want != cur_status:
            self.set_status(rid, want)
        if it.done and it.spent_days and not (self.get(rid) or {}).get("actual_days"):
            self.reconcile(rid, it.spent_days, note="Jira 耗费时间回填")
        return rid, created

    def attach_jira(self, req_id: int, a) -> None:
        """零散需求那条转成 Jira 工单评估后，把编码阶段人工 / AI 挂回原来那行账（不另开一行）。"""
        if not self.get(req_id):
            return
        self.conn.execute("UPDATE requirements SET coding_trad=?, coding_ai=? WHERE id=?", (a.coding_trad, a.coding_ai, req_id))
        self.conn.commit()
        self.event(req_id, "转 Jira 评估", f"传统 {a.mid} 人天 · AI 编码 {a.coding_ai}（编码阶段 {a.coding_trad}）· 复杂度 {a.level} {a.total}")

    def jira_done_issues(self) -> list[JiraIssue]:
        """对过账的 Jira 工单 → 下次评估的校准样本（Jira 自己的估算飞轮，不混进零散需求的历史库）。"""
        out = []
        fields = {f.name for f in dataclasses.fields(JiraIssue)}
        for payload, actual in self.conn.execute(
                f"SELECT payload, actual_days FROM requirements WHERE source='{JIRA_SOURCE}' AND actual_days IS NOT NULL AND actual_days>0"):
            try:
                d = (json.loads(payload or "{}").get("jira") or {})
            except json.JSONDecodeError:
                continue
            it = JiraIssue(**{k: v for k, v in d.items() if k in fields})
            it.status, it.spent_h = "完成", float(actual) * HOURS_PER_DAY
            out.append(it)
        return out

    def update_estimate(self, req_id: int, estimate) -> None:
        row = self.get(req_id)
        if not row:
            raise ValueError(f"台账里没有编号 {req_id}")
        payload = json.loads(row.get("payload") or "{}")
        previous = payload.get("estimate")
        current = estimate_snapshot(estimate)
        payload["estimate"] = current
        # 当前结果与事件历史一起提交，既清掉失败后的旧数字，也保留重算前快照。
        with self.conn:
            self.conn.execute(
                "UPDATE requirements SET mid_days=?, ai_days=?, confidence=?, payload=? WHERE id=?",
                (current.get("mid"), current.get("ai_mid"), current.get("confidence", ""),
                 json.dumps(payload, ensure_ascii=False), req_id),
            )
            self.conn.execute(
                "INSERT INTO events(ts,req_id,action,detail) VALUES(?,?,?,?)",
                (datetime.now().isoformat(timespec="seconds"), req_id, "估量重算",
                 json.dumps({"previous": previous, "current": current}, ensure_ascii=False)),
            )

    def update_analysis(self, req_id: int, analysis, label: str = "模型增强") -> int:
        """把后台增强结果更新到同一条需求，保留其状态和审计记录；另存一版（内容没变不另存）。返回最新版本号。"""
        self.update_estimate(req_id, analysis.estimate)
        row = self.get(req_id)
        payload = json.loads(row.get("payload") or "{}")
        payload.update(card=json.loads(analysis.card.to_json()), questions=[q.__dict__ for q in analysis.questions],
                       decisions=[d.recommended for d in analysis.architecture.decisions],
                       data_map=[r.__dict__ for r in getattr(analysis.architecture, "data_map", [])])
        with self.conn:
            self.conn.execute(
                "UPDATE requirements SET title=?,engine=?,n_questions=?,n_answered=?,n_high_open=?,seconds=?,payload=? WHERE id=?",
                (analysis.card.title, analysis.engine, len(analysis.questions),
                 sum(bool(q.answer.strip()) for q in analysis.questions), analysis.estimate.unanswered_high,
                 analysis.seconds, json.dumps(payload, ensure_ascii=False), req_id),
            )
        return self.add_version(req_id, analysis, label)

    def record_change(self, req_id: int, analysis, channel: str = "") -> int:
        """需求变更：原话改了，同一编号换成新原话的分析，记「需求变更」流水并存一版。返回最新版本号。
        受理时间、来源、提出方与状态不动（还是这条需求）；类型与脱敏处数跟着新原话走。"""
        if not self.get(req_id):
            raise ValueError(f"台账里没有编号 {req_id}")
        before = self.latest_version(req_id)
        with self.conn:
            self.conn.execute("UPDATE requirements SET req_type=?, redacted=? WHERE id=?",
                              (analysis.card.req_type, int(getattr(analysis, "redacted", 0) or 0), req_id))
        n = self.update_analysis(req_id, analysis, label="需求变更")
        if before and n > before["version"]:
            found = self.versions_of(req_id)
            old = found[versions.base_index(found)]   # 和上一份同类结果比，不拿模型改写的说法当变更
            d = versions.diff(old["snapshot"], found[-1]["snapshot"], old["version"], n)
            detail = f"记为 v{n}，{d.summary}"
        elif before:
            detail = f"原话与分析结果同 v{n}，未另存版本"
        else:
            detail = f"记为 v{n}"
        self.event(req_id, "需求变更", detail + (f"（{channel}）" if channel else ""))
        return n

    def add_version(self, req_id: int, analysis, label: str = "") -> int:
        """存一版分析快照（analysis 也可直接给快照 dict）。和最新一版比不出差别（顺序变化不算）就不另存。
        返回本条最新的版本号（从 1 起）；台账里没有这条需求、或它是 Jira 工单（走「Jira 重新评估」）返回 0。"""
        req_id = int(req_id or 0)
        row = self.conn.execute("SELECT source FROM requirements WHERE id=?", (req_id,)).fetchone() if req_id else None
        if not row or row[0] == JIRA_SOURCE:
            return 0
        body = json.dumps(analysis if isinstance(analysis, dict) else versions.snapshot(analysis), ensure_ascii=False)
        count, last = self.conn.execute(
            "SELECT COUNT(*), (SELECT snapshot FROM versions WHERE req_id=? ORDER BY id DESC LIMIT 1) FROM versions WHERE req_id=?",
            (req_id, req_id)).fetchone()
        try:
            if last and versions.same(json.loads(last), json.loads(body)):
                return int(count)
        except (ValueError, TypeError, AttributeError):
            pass   # 旧版快照坏了就照常另存
        with self.conn:
            self.conn.execute("INSERT INTO versions(req_id,ts,label,snapshot) VALUES(?,?,?,?)",
                              (req_id, datetime.now().isoformat(timespec="seconds"), label, body))
        return int(count) + 1

    def event(self, req_id: int, action: str, detail: str = "") -> None:
        self.conn.execute("INSERT INTO events(ts,req_id,action,detail) VALUES(?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), req_id, action, detail))
        self.conn.commit()

    def feedback(self, req_id: int, skill: str, score: int, note: str = "") -> None:
        self.conn.execute("INSERT INTO feedback(ts,req_id,skill,score,note) VALUES(?,?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), req_id, skill, score, note))
        self.conn.commit()

    def update_answers(self, req_id: int, n_answered: int, n_high_open: int) -> None:
        """业务答复重算后同步已答复数与未答复高影响数；受理 → 已确认。"""
        self.conn.execute("UPDATE requirements SET n_answered=?, n_high_open=?, status=CASE WHEN status='受理' AND ?>0 THEN '已确认' ELSE status END WHERE id=?",
                          (n_answered, n_high_open, n_answered, req_id))
        self.conn.commit()

    def set_status(self, req_id: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"未知状态：{status}")
        self.conn.execute("UPDATE requirements SET status=? WHERE id=?", (status, req_id))
        self.conn.commit()
        self.event(req_id, "状态", status)

    def reconcile(self, req_id: int, actual_days: float, ai_assisted: bool = False, ai_share: float = 0.0, note: str = "",
                  write_history: bool = True) -> dict:
        """对账：回写实际人天 → 状态"已对账" → 追加到历史需求库（估算飞轮）。返回偏差信息。"""
        row = self.get(req_id)
        if not row:
            raise ValueError(f"台账里没有编号 {req_id}")
        actual = float(actual_days)
        if not math.isfinite(actual) or actual <= 0:
            raise ValueError("实际工时必须是大于 0 的有限数值")
        self.conn.execute(
            "UPDATE requirements SET actual_days=?, ai_assisted=?, ai_share=?, closed_ts=?, close_note=?, status='已对账' WHERE id=?",
            (actual, 1 if ai_assisted else 0, float(ai_share or 0.0), datetime.now().isoformat(timespec="seconds"), note or "", req_id))
        self.conn.commit()
        trad, ai = _number(row.get("mid_days")), _number(row.get("ai_days"))
        valid = trad is not None and trad > 0 and ai is not None and ai > 0
        est = (ai if ai_assisted else trad) if valid else None
        dev = (est - actual) / actual if est is not None else None
        detail = (f"实际 {actual} 人天，估 {est}，偏差 {dev:+.0%}" if est is not None
                  else f"实际 {actual} 人天；尚无有效两轨估量，未计算偏差或回写估算历史")
        self.event(req_id, "对账", detail + ("，AI 协同" if ai_assisted else ""))
        hist_id = ""
        if write_history and valid and row.get("source") != JIRA_SOURCE:   # Jira 工单走自己的校准样本（jira_done_issues），不写零散需求的历史库
            hist_id = self._append_history(row, actual, ai_assisted, ai_share, note)
        return {"estimate": est, "actual": actual, "deviation": dev, "history_id": hist_id}

    def _append_history(self, row: dict, actual: float, ai_assisted: bool, ai_share: float, note: str) -> str:
        """把对账后的需求追加进 history_learned.json（与样本库合并加载，样本文件不动），并清缓存让下次估量即刻用上。"""
        from . import knowledge

        path = config.HISTORY_LEARNED_PATH
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"_说明": "需知台账对账回写的真实需求（估算飞轮）；与 history_requirements.json 合并使用，同 id 以本文件为准。", "items": []}
        items = data.setdefault("items", [])
        try:
            payload = json.loads(row.get("payload") or "{}")
        except json.JSONDecodeError:
            payload = {}
        card = payload.get("card") or {}
        kws = list(dict.fromkeys([*(card.get("symbols") or []), *(card.get("indicators") or []), *(card.get("scope_objects") or []),
                                  *[d.split("（")[0] for d in (card.get("data_sources") or [])], row.get("req_type") or ""]))
        kws = [k for k in kws if k][:10]
        hid = f"L{int(row['id']):03d}"
        items = [h for h in items if h.get("id") != hid]
        items.append({
            "id": hid, "year": datetime.now().year, "title": row.get("title") or "", "type": row.get("req_type") or "",
            "dept": row.get("dept") or _dept_of(row.get("requester") or ""), "keywords": kws,
            "estimate_days": float(row.get("mid_days") or 0.0), "actual_days": float(actual),
            "note": (note or "") + ("　" if note else "") + "来源：需知台账对账", "ai_assisted": bool(ai_assisted), "ai_share": float(ai_share or 0.0),
            "source": "台账对账",
        })
        data["items"] = items
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        knowledge.history.cache_clear()
        return hid

    # ---------- 读 ----------
    def get(self, req_id: int) -> dict | None:
        cur = self.conn.execute("SELECT * FROM requirements WHERE id=?", (req_id,))
        r = cur.fetchone()
        if not r:
            return None
        return dict(zip([c[0] for c in cur.description], r))

    def recent(self, n: int = 20) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id,ts,title,dept,source,req_type,requester,engine,status,n_questions,n_answered,n_high_open,mid_days,ai_days,coding_ai,actual_days,close_note,ai_share,confidence,redacted,ext_key "
            "FROM requirements ORDER BY id DESC LIMIT ?", (n,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def events_of(self, req_id: int) -> list[dict]:
        cur = self.conn.execute("SELECT ts,action,detail FROM events WHERE req_id=? ORDER BY id", (req_id,))
        return [{"ts": t, "action": a, "detail": d} for t, a, d in cur.fetchall()]

    def versions_of(self, req_id: int) -> list[dict]:
        """本条全部版本（旧 → 新）：版本号（1 起）、时间、动作、快照。"""
        out = []
        rows = self.conn.execute("SELECT ts, label, snapshot FROM versions WHERE req_id=? ORDER BY id", (int(req_id or 0),)).fetchall()
        for n, (ts, label, body) in enumerate(rows, 1):
            try:
                snap = json.loads(body or "{}")
            except ValueError:
                snap = {}
            out.append({"version": n, "ts": ts or "", "label": label or "", "snapshot": snap if isinstance(snap, dict) else {}})
        return out

    def latest_version(self, req_id: int) -> dict | None:
        found = self.versions_of(req_id)
        return found[-1] if found else None

    def export_rows(self) -> list[dict]:
        """审计导出：全部需求行（不含 payload 原文）。"""
        cur = self.conn.execute(
            "SELECT id,ts,title,dept,requester,source,req_type,engine,status,n_questions,n_answered,n_high_open,mid_days,ai_days,coding_trad,coding_ai,"
            "actual_days,ai_assisted,ai_share,confidence,redacted,seconds,closed_ts,close_note,ext_key FROM requirements ORDER BY id")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def stats(self) -> dict:
        n, q, ans, red = self.conn.execute("SELECT COUNT(*), COALESCE(SUM(n_questions),0), COALESCE(SUM(n_answered),0), COALESCE(SUM(redacted),0) FROM requirements").fetchone()
        return {"count": n, "questions": q, "answered": ans, "redacted": red, "minutes_saved": n * sum(MINUTES_SAVED.values())}

    def dashboard(self) -> dict:
        """经营看板：需求量（部门 / 类型 / 月份 / 状态）、澄清效率、估算偏差、AI 协同省时、反馈。全部由台账算出。"""
        c = self.conn
        n = c.execute("SELECT COUNT(*) FROM requirements").fetchone()[0]
        out: dict = {"count": n, "by_dept": {}, "by_type": {}, "by_month": {}, "by_status": {s: 0 for s in STATUSES}}
        if not n:
            out.update({"avg_questions": 0.0, "answer_rate": 0.0, "avg_rounds": 0.0, "avg_seconds": 0.0, "high_open_rate": 0.0,
                        "trad_days": None, "ai_days": None, "saved_days": None, "saving_pct": None,
                        "estimated_count": 0, "unestimated_count": 0, "legacy_count": 0,
                        "jira_count": 0, "jira_coding_trad": 0.0, "jira_coding_ai": 0.0, "jira_by_kind": {},
                        "closed": 0, "comparable_count": 0, "mape": None, "mape_trad": None, "mape_ai": None, "bias_trad": None, "thumbs_up": 0, "thumbs_down": 0, "depts": 0})
            return out
        # 部门与类型分布只算零散需求：Jira 工单没有提出部门，类型（需求 / 缺陷…）也和业务类型（报表 / 页面…）不是一个口径
        for dept, k in c.execute(f"SELECT COALESCE(NULLIF(dept,''),'未注明'), COUNT(*) FROM requirements WHERE {_NOT_JIRA} GROUP BY 1 ORDER BY 2 DESC"):
            out["by_dept"][dept] = k
        for t, k in c.execute(f"SELECT COALESCE(NULLIF(req_type,''),'其他'), COUNT(*) FROM requirements WHERE {_NOT_JIRA} GROUP BY 1 ORDER BY 2 DESC"):
            out["by_type"][t] = k
        out["jira_by_kind"] = dict(c.execute(
            f"SELECT COALESCE(NULLIF(req_type,''),'其他'), COUNT(*) FROM requirements WHERE source='{JIRA_SOURCE}' GROUP BY 1 ORDER BY 2 DESC").fetchall())
        for m, k in c.execute("SELECT substr(ts,1,7), COUNT(*) FROM requirements GROUP BY 1 ORDER BY 1"):
            out["by_month"][m] = k
        for s, k in c.execute("SELECT COALESCE(NULLIF(status,''),'受理'), COUNT(*) FROM requirements GROUP BY 1"):
            out["by_status"][s] = out["by_status"].get(s, 0) + k
        # 追问 / 澄清 / 出稿用时只算零散需求（Jira 工单已写好，不追问）；工时与省时全算
        n_free = c.execute(f"SELECT COUNT(*) FROM requirements WHERE {_NOT_JIRA}").fetchone()[0]
        aq, sa, sq, ho, sec = c.execute(
            "SELECT AVG(n_questions), SUM(n_answered), SUM(n_questions), SUM(COALESCE(n_high_open,0)), AVG(COALESCE(seconds,0)) "
            f"FROM requirements WHERE {_NOT_JIRA}").fetchone()
        valid_estimates: list[tuple[float, float]] = []
        legacy_count = 0
        for before, after, payload in c.execute("SELECT mid_days, ai_days, payload FROM requirements"):
            before, after = _number(before), _number(after)
            if before is None or before <= 0 or after is None or after <= 0:
                continue
            try:
                snapshot = json.loads(payload or "{}").get("estimate") or {}
            except (ValueError, TypeError):
                snapshot = {}
            if snapshot.get("method") == "llm" and snapshot.get("status") == "ready":
                valid_estimates.append((before, after))
            elif snapshot.get("method") not in ("llm", "unavailable"):
                legacy_count += 1
        out["estimated_count"] = len(valid_estimates)
        out["legacy_count"] = legacy_count
        out["unestimated_count"] = n - len(valid_estimates) - legacy_count
        trad = sum(pair[0] for pair in valid_estimates) if valid_estimates else None
        ai = sum(pair[1] for pair in valid_estimates) if valid_estimates else None
        rounds = c.execute("SELECT COUNT(*) FROM events WHERE action='业务答复重算'").fetchone()[0]
        jn, jct, jca = c.execute(
            f"SELECT COUNT(*), SUM(COALESCE(coding_trad,0)), SUM(COALESCE(coding_ai,0)) FROM requirements WHERE source='{JIRA_SOURCE}'").fetchone()
        out["jira_count"], out["jira_coding_trad"], out["jira_coding_ai"] = int(jn or 0), round(jct or 0.0, 1), round(jca or 0.0, 1)
        out["avg_questions"] = round(aq or 0.0, 1)
        out["answer_rate"] = round((sa or 0) / sq, 2) if sq else 0.0
        out["avg_rounds"] = round(1 + rounds / n_free, 1) if n_free else 0.0   # 开工一轮 + 每次答复重算算一轮
        out["avg_seconds"] = round(sec or 0.0, 1)
        out["high_open_rate"] = round((ho or 0) / sq, 2) if sq else 0.0
        out["trad_days"] = round(trad, 3) if trad is not None else None
        out["ai_days"] = round(ai, 3) if ai is not None else None
        out["saved_days"] = round(trad - ai, 3) if trad is not None and ai is not None else None
        out["saving_pct"] = round(100 * (trad - ai) / trad) if trad is not None and ai is not None else None
        out["depts"] = len(out["by_dept"])
        closed = c.execute("SELECT mid_days, ai_days, actual_days, ai_assisted FROM requirements WHERE actual_days IS NOT NULL AND actual_days>0").fetchall()
        out["closed"] = len(closed)
        if closed:
            # 用了 AI 协同的比 AI 轨估算，没用的比传统轨：拿 AI 做完的实际人天去比传统估算，偏差会被夸大
            comparable = [(m, ai, a, used) for m, ai, a, used in closed if m is not None and m > 0 and ai is not None and ai > 0]
            out["comparable_count"] = len(comparable)
            errs_t = [abs(m - a) / a for m, _, a, used in comparable if not used]
            errs_a = [abs(ai - a) / a for _, ai, a, used in comparable if used]
            bias = [(m - a) / a for m, _, a, used in comparable if not used]
            both = errs_t + errs_a
            out["mape"] = round(100 * sum(both) / len(both)) if both else None
            out["mape_trad"] = round(100 * sum(errs_t) / len(errs_t)) if errs_t else None
            out["mape_ai"] = round(100 * sum(errs_a) / len(errs_a)) if errs_a else None
            out["bias_trad"] = round(100 * sum(bias) / len(bias)) if bias else None
        else:
            out["comparable_count"] = 0
            out["mape"] = out["mape_trad"] = out["mape_ai"] = out["bias_trad"] = None
        up, down = c.execute("SELECT SUM(CASE WHEN score>0 THEN 1 ELSE 0 END), SUM(CASE WHEN score<0 THEN 1 ELSE 0 END) FROM feedback").fetchone()
        out["thumbs_up"], out["thumbs_down"] = int(up or 0), int(down or 0)
        return out

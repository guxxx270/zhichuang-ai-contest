"""需求一本账（台账）：SQLite 记录每一次分析（卡片 / 清单 / 估算 / 决策）、业务答复、状态流转、对账与反馈。
既是审计留痕，也是估算飞轮的数据：对账回写实际人天 → 追加到历史需求库 → 下次估量自动校准；看板按部门 / 类型 / 状态汇总。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from . import config

MINUTES_SAVED = {"问清": 60, "写单": 90, "估量": 30, "定架": 60, "出样": 120}   # 与人工相比的估计节省，方案文档里需校准

STATUSES = ["受理", "已确认", "开发中", "已交付", "已对账"]


def _dept_of(requester: str) -> str:
    r = (requester or "").strip()
    if not r:
        return "未注明"
    return r.split("·")[0].split("/")[0].strip() or "未注明"


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
        """)
        # 逐列补齐（老库无该列时才加）
        for col, typ in (("ai_days", "REAL"), ("dept", "TEXT"), ("status", "TEXT"), ("actual_days", "REAL"),
                         ("ai_assisted", "INTEGER"), ("ai_share", "REAL"), ("closed_ts", "TEXT"), ("close_note", "TEXT"),
                         ("seconds", "REAL"), ("n_high_open", "INTEGER")):
            try:
                self.conn.execute(f"ALTER TABLE requirements ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass   # 已有该列
        self.conn.commit()

    # ---------- 写 ----------
    def log_analysis(self, a) -> int:
        cur = self.conn.execute(
            "INSERT INTO requirements(ts,title,source,req_type,requester,engine,n_questions,n_answered,mid_days,confidence,redacted,ai_days,"
            "dept,status,seconds,n_high_open,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), a.card.title, a.card.source, a.card.req_type, a.card.requester, a.card.engine,
             len(a.questions), sum(1 for q in a.questions if q.answer.strip()), a.estimate.mid, a.estimate.confidence, a.redacted, a.estimate.ai_mid,
             _dept_of(a.card.requester), "受理", float(getattr(a, "seconds", 0.0) or 0.0), int(getattr(a.estimate, "unanswered_high", 0) or 0),
             json.dumps({"card": json.loads(a.card.to_json()), "questions": [q.__dict__ for q in a.questions],
                         "estimate": {"mid": a.estimate.mid, "low": a.estimate.low, "high": a.estimate.high, "ai_mid": a.estimate.ai_mid, "dims": a.estimate.dims},
                         "decisions": [d.recommended for d in a.architecture.decisions],
                         "data_map": [r.__dict__ for r in getattr(a.architecture, "data_map", [])]}, ensure_ascii=False)))
        self.conn.commit()
        return int(cur.lastrowid)

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
        self.conn.execute(
            "UPDATE requirements SET actual_days=?, ai_assisted=?, ai_share=?, closed_ts=?, close_note=?, status='已对账' WHERE id=?",
            (actual, 1 if ai_assisted else 0, float(ai_share or 0.0), datetime.now().isoformat(timespec="seconds"), note or "", req_id))
        self.conn.commit()
        est = float(row.get("ai_days") if ai_assisted and row.get("ai_days") else (row.get("mid_days") or 0.0))
        dev = (est - actual) / actual if actual else 0.0
        self.event(req_id, "对账", f"实际 {actual} 人天，估 {est}，偏差 {dev:+.0%}" + ("，AI 协同" if ai_assisted else ""))
        hist_id = ""
        if write_history:
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
            "SELECT id,ts,title,dept,source,req_type,requester,engine,status,n_questions,n_answered,n_high_open,mid_days,ai_days,actual_days,confidence,redacted "
            "FROM requirements ORDER BY id DESC LIMIT ?", (n,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def events_of(self, req_id: int) -> list[dict]:
        cur = self.conn.execute("SELECT ts,action,detail FROM events WHERE req_id=? ORDER BY id", (req_id,))
        return [{"ts": t, "action": a, "detail": d} for t, a, d in cur.fetchall()]

    def export_rows(self) -> list[dict]:
        """审计导出：全部需求行（不含 payload 原文）。"""
        cur = self.conn.execute(
            "SELECT id,ts,title,dept,requester,source,req_type,engine,status,n_questions,n_answered,n_high_open,mid_days,ai_days,actual_days,"
            "ai_assisted,ai_share,confidence,redacted,seconds,closed_ts,close_note FROM requirements ORDER BY id")
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
                        "trad_days": 0.0, "ai_days": 0.0, "saved_days": 0.0, "saving_pct": 0.0,
                        "closed": 0, "mape_trad": None, "mape_ai": None, "bias_trad": None, "thumbs_up": 0, "thumbs_down": 0, "depts": 0})
            return out
        for dept, k in c.execute("SELECT COALESCE(NULLIF(dept,''),'未注明'), COUNT(*) FROM requirements GROUP BY 1 ORDER BY 2 DESC"):
            out["by_dept"][dept] = k
        for t, k in c.execute("SELECT COALESCE(NULLIF(req_type,''),'其他'), COUNT(*) FROM requirements GROUP BY 1 ORDER BY 2 DESC"):
            out["by_type"][t] = k
        for m, k in c.execute("SELECT substr(ts,1,7), COUNT(*) FROM requirements GROUP BY 1 ORDER BY 1"):
            out["by_month"][m] = k
        for s, k in c.execute("SELECT COALESCE(NULLIF(status,''),'受理'), COUNT(*) FROM requirements GROUP BY 1"):
            out["by_status"][s] = out["by_status"].get(s, 0) + k
        aq, sa, sq, ho, sec, trad, ai = c.execute(
            "SELECT AVG(n_questions), SUM(n_answered), SUM(n_questions), SUM(COALESCE(n_high_open,0)), AVG(COALESCE(seconds,0)), "
            "SUM(COALESCE(mid_days,0)), SUM(COALESCE(ai_days, mid_days, 0)) FROM requirements").fetchone()
        rounds = c.execute("SELECT COUNT(*) FROM events WHERE action='业务答复重算'").fetchone()[0]
        out["avg_questions"] = round(aq or 0.0, 1)
        out["answer_rate"] = round((sa or 0) / sq, 2) if sq else 0.0
        out["avg_rounds"] = round(1 + rounds / n, 1)          # 开工一轮 + 每次答复重算算一轮
        out["avg_seconds"] = round(sec or 0.0, 1)
        out["high_open_rate"] = round((ho or 0) / sq, 2) if sq else 0.0
        out["trad_days"] = round(trad or 0.0, 1)
        out["ai_days"] = round(ai or 0.0, 1)
        out["saved_days"] = round((trad or 0.0) - (ai or 0.0), 1)
        out["saving_pct"] = round(100 * out["saved_days"] / trad) if trad else 0.0
        out["depts"] = len(out["by_dept"])
        closed = c.execute("SELECT mid_days, ai_days, actual_days, ai_assisted FROM requirements WHERE actual_days IS NOT NULL AND actual_days>0").fetchall()
        out["closed"] = len(closed)
        if closed:
            errs_t = [abs(m - a) / a for m, _, a, _ in closed if m]
            errs_a = [abs(ai - a) / a for _, ai, a, used in closed if used and ai]
            bias = [(m - a) / a for m, _, a, _ in closed if m]
            out["mape_trad"] = round(100 * sum(errs_t) / len(errs_t)) if errs_t else None
            out["mape_ai"] = round(100 * sum(errs_a) / len(errs_a)) if errs_a else None
            out["bias_trad"] = round(100 * sum(bias) / len(bias)) if bias else None
        else:
            out["mape_trad"] = out["mape_ai"] = out["bias_trad"] = None
        up, down = c.execute("SELECT SUM(CASE WHEN score>0 THEN 1 ELSE 0 END), SUM(CASE WHEN score<0 THEN 1 ELSE 0 END) FROM feedback").fetchone()
        out["thumbs_up"], out["thumbs_down"] = int(up or 0), int(down or 0)
        return out

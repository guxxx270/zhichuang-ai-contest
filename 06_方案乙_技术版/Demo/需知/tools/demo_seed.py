"""演示数据：把一本账恢复成上台用的预置状态（上台前双击 重置演示数据.command）。

做什么：
  1. 当前一本账（data/ledger.sqlite3：需求 / 流水 / 追问记忆 / 模型调用审计）与 data/history_learned.json 先整份备份到 data/backup/<时间>/；
  2. 清空上述数据（就地清表，需知页面开着也不会写到旧文件里）；
  3. 用评测集里的虚构需求 + 本文件 6 条虚构需求，真实跑一遍规则引擎（不调模型）记进一本账，
     再按计划补上业务答复、状态流转与对账，时间分散到近 8 周 —— 看板、催办摘要、追问记忆都有内容。

刻意不做的：
  - 不预置"模型调用审计"：那张表只记真实调用。现场点一次「模型润色」就会出现一行。
  - 不写 history_learned.json：对账用 write_history=False，样例 ① 的估算（8.5 → 5.4 人天）保持与 PPT 一致。
  - 不预置资管运营的需求与追问记忆、不收录四个演示样例：现场开工的样例 ① 是全新一条，第二条同提出方需求才出现 🧠。

所有数据均为虚构，看板数字只用于演示界面，不是试运行成绩。

用法：
    python tools/demo_seed.py            # 备份 → 清空 → 预置
    python tools/demo_seed.py --empty    # 备份 → 清空（不预置，一本账从 0 开始）
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xuzhi import config  # noqa: E402
from xuzhi.ledger import Ledger  # noqa: E402
from xuzhi.llm import LLM  # noqa: E402
from xuzhi.memory import Memory  # noqa: E402
from xuzhi.pipeline import analyze  # noqa: E402

NOTE = "演示数据（虚构）"

# 评测集之外补几条，让研究所 / 风控部 / 资管部 / 合规部 / 投资部的量更像真实分布（全部虚构）
EXTRA = {
    "X1": "来源：企业微信 · 研究所 研究员 小陈 → 技术部\n\n每天收盘后能不能自动出一张黑色系主力合约的持仓排名变化表？"
          "前二十名会员的多空增减，螺纹、热卷、铁矿都要，发到研究群里。",
    "X2": "来源：投研晨会纪要（2026-08-28）摘录 · 研究所 能化组\n\n能化组提出：原油、PTA、甲醇的产业链利润做个日度看板，"
          "现货价格从资讯终端取，期货用主力合约收盘价，图表按周对比。",
    "X3": "来源：企业微信 · 风控部 老刘 → 技术部\n\n客户风险度超过 100% 要追保的时候，能不能自动提醒客户经理和我们？"
          "盘中每半小时查一次，夜盘也要，企业微信提醒就行。",
    "X4": "来源：企业微信 · 资管部 投资经理 赵经理 → 技术部\n\n各产品每天的期货持仓保证金占比和杠杆率，能不能出个日报，"
          "超过合同约定上限的标红，盘后发给我和风控。",
    "X5": "来源：邮件 · 合规部 周经理 → 技术部\n主题：营销材料预审登记\n\n能不能做个营销材料预审登记页面？营业部对外发的材料先上传登记，"
          "填用途和投放渠道，合规审核意见留痕，审核通过才能下载使用。",
    "X6": "来源：企业微信 · 投资部 投资经理 钱经理 → 技术部\n\n套利组合的每日盈亏归因能不能自动算？按品种和腿拆开，日终出结果，Excel 和页面都要。",
}

# (需求, 几天前受理, 最终状态, 业务答了几条, 实际/估算 系数, 是否用了 AI 协同, AI 参与度) —— 由旧到新
PLAN = [
    ("E10", 55, "已对账", 2, 1.15, False, 0.0),
    ("E05", 52, "已对账", 3, 0.90, True, 0.4),
    ("E09", 49, "已对账", 3, 1.20, False, 0.0),
    ("X1", 46, "已对账", 2, 1.05, True, 0.5),
    ("E06", 42, "已对账", 3, 0.95, True, 0.35),
    ("E11", 38, "已对账", 2, 0.85, True, 0.6),
    ("X4", 35, "已对账", 2, 1.10, True, 0.3),
    ("E13", 31, "已交付", 2, 0, False, 0.0),
    ("E07", 28, "开发中", 3, 0, False, 0.0),
    ("X3", 24, "开发中", 3, 0, False, 0.0),
    ("E12", 21, "开发中", 0, 0, False, 0.0),
    ("E20", 17, "已交付", 2, 0, False, 0.0),
    ("X2", 14, "已确认", 2, 0, False, 0.0),
    ("E14", 10, "已确认", 3, 0, False, 0.0),
    ("E16", 8, "已确认", 2, 0, False, 0.0),
    ("X5", 6, "受理", 0, 0, False, 0.0),
    ("E18", 4, "受理", 0, 0, False, 0.0),
    ("X6", 2, "受理", 0, 0, False, 0.0),
    ("E15", 1, "受理", 0, 0, False, 0.0),
    ("E08", 0, "受理", 0, 0, False, 0.0),
]

_STEPS = ["已确认", "开发中", "已交付"]


def _texts() -> dict[str, str]:
    cases = json.loads((config.DATA_DIR / "eval" / "eval_set.json").read_text(encoding="utf-8"))["cases"]
    return {**{c["id"]: c["text"] for c in cases}, **EXTRA}


def backup() -> Path | None:
    files = [p for p in (config.LEDGER_DB, config.HISTORY_LEARNED_PATH) if p.exists()]
    if not files:
        return None
    dest = config.LEDGER_DB.parent / "backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for p in files:
        if p.suffix == ".sqlite3":   # 用 SQLite 自带备份，页面开着也能拿到一致的副本
            src, dst = sqlite3.connect(str(p)), sqlite3.connect(str(dest / p.name))
            src.backup(dst)
            dst.close()
            src.close()
        else:
            shutil.copy2(p, dest / p.name)
    return dest


def clear() -> None:
    Ledger(), Memory()   # 确保表都在（老库 / 空库）
    conn = sqlite3.connect(str(config.LEDGER_DB))
    tables = [t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in ("requirements", "events", "feedback", "answer_memory", "llm_calls"):
        if t in tables:
            conn.execute(f"DELETE FROM {t}")
    if "sqlite_sequence" in tables:
        conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.close()
    config.HISTORY_LEARNED_PATH.unlink(missing_ok=True)


def _at(now: datetime, days_ago: int, i: int) -> datetime:
    base = (now - timedelta(days=days_ago)).replace(hour=9 + (i * 3) % 8, minute=(i * 17) % 60, second=0, microsecond=0)
    return min(base, now - timedelta(minutes=30 + i))


def seed(now: datetime | None = None) -> dict:
    now = now or datetime.now()
    texts = _texts()
    ledger, memory = Ledger(), Memory()
    llm = LLM(mode="mock", audit=False)
    for i, (key, days_ago, status, n_ans, factor, ai, share) in enumerate(PLAN):
        text = texts[key]
        a = analyze(text, llm=llm, memory=memory)
        rid = ledger.log_analysis(a)
        if n_ans:
            picked = [q for q in a.questions if q.id != "C_POS" and not q.id.startswith("L") and not q.recalled][:n_ans]
            answers = {q.id: q.default for q in picked}
            a = analyze(text, llm=llm, answers=answers, memory=memory)
            ledger.event(rid, "业务答复重算", f"{len(answers)} 条")
            ledger.update_answers(rid, sum(1 for q in a.questions if q.answer.strip()), int(getattr(a.estimate, "unanswered_high", 0) or 0))
            learned = memory.learn(a.card, a.questions, rid)
            if learned:
                ledger.event(rid, "追问记忆", f"记住 {learned} 条口径（{a.card.requester or '未注明'}）")
        target = 4 if status == "已对账" else (_STEPS.index(status) + 1 if status in _STEPS else 0)
        for s in _STEPS[:target]:
            if s == "已确认" and (ledger.get(rid) or {}).get("status") == "已确认":
                continue
            ledger.set_status(rid, s)
        if status == "已对账":
            row = ledger.get(rid) or {}
            est = float(row.get("ai_days") if ai and row.get("ai_days") else row.get("mid_days") or 0.0)
            ledger.reconcile(rid, round(est * factor, 1), ai_assisted=ai, ai_share=share, note=NOTE, write_history=False)
        _backdate(ledger, memory, rid, _at(now, days_ago, i), days_ago, now)
    return {"count": len(PLAN), "dashboard": ledger.dashboard(), "memory": memory.stats()}


def _backdate(ledger: Ledger, memory: Memory, rid: int, t0: datetime, days_ago: int, now: datetime) -> None:
    """受理时间挪到计划日期；之后的流水按顺序摊在受理到"现在"之间（最多 20 天）。"""
    span = max(min(days_ago, 20), 0)
    ledger.conn.execute("UPDATE requirements SET ts=? WHERE id=?", (t0.isoformat(timespec="seconds"), rid))
    evs = ledger.conn.execute("SELECT id, action FROM events WHERE req_id=? ORDER BY id", (rid,)).fetchall()
    for k, (eid, _action) in enumerate(evs, 1):
        t = min(t0 + timedelta(hours=2) + timedelta(days=span * k / (len(evs) + 1)), now - timedelta(minutes=5))
        ledger.conn.execute("UPDATE events SET ts=? WHERE id=?", (t.isoformat(timespec="seconds"), eid))
        if _action == "对账":
            ledger.conn.execute("UPDATE requirements SET closed_ts=? WHERE id=?", (t.isoformat(timespec="seconds"), rid))
    ledger.conn.commit()
    memory.conn.execute("UPDATE answer_memory SET ts=? WHERE req_id=?", ((t0 + timedelta(hours=2)).isoformat(timespec="seconds"), rid))
    memory.conn.commit()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把一本账恢复成演示用的预置状态（先备份）")
    ap.add_argument("--empty", action="store_true", help="只清空，不预置")
    args = ap.parse_args(argv)
    dest = backup()
    print(f"已备份到 {dest}" if dest else "当前没有一本账数据，无需备份")
    clear()
    if args.empty:
        print("一本账已清空（未预置）。")
        return 0
    out = seed()
    d, m = out["dashboard"], out["memory"]
    print(f"已预置 {out['count']} 条虚构需求：{d['depts']} 个部门；状态 " + " / ".join(f"{k} {v}" for k, v in d["by_status"].items()))
    print(f"看板：平均追问 {d['avg_questions']} 条，平均澄清 {d['avg_rounds']} 轮，已对账 {d['closed']} 条（估算偏差 {d['mape']}%），"
          f"AI 协同省时（估）{d['saved_days']} 人天；追问记忆 {m['entries']} 条口径、省问 {m['hits']} 次")
    print("模型调用审计为空（只记真实调用）；样例 ① 与资管运营的追问记忆保持全新。需知页面开着的话，刷新一下即可。")
    print("提醒：以上均为虚构演示数据，不要当作试运行成绩讲。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

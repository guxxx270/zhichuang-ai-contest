"""追问记忆：问过的不再问。"""
from __future__ import annotations

import threading

import pytest

from xuzhi.memory import Memory, memorable
from xuzhi.pipeline import analyze
from xuzhi.pipeline.intake import Card, Question, confirm_message

SAMPLE_1 = "资管运营小周：净值日报能不能加一列'较上一交易日变动'，风险指标也放进去，周五给客户那版也要，最好下周就能用。"
SAMPLE_2 = "资管运营：净值日报再加一列累计净值，也要给客户版，下周要。"


@pytest.fixture
def mem(tmp_path):
    return Memory(tmp_path / "mem.sqlite3")


def test_memorable_rules():
    assert memorable(Question("T03", "口径", "期货", "高", "q?", "w", "d", answer="按结算价"))
    assert not memorable(Question("T03", "口径", "期货", "高", "q?", "w", "d", answer="  "))
    assert not memorable(Question("C_POS", "界面", "通用", "中", "q?", "w", "d", answer="表尾"))      # 位置题每条需求不同
    assert not memorable(Question("L1", "补充", "模型", "中", "q?", "w", "d", answer="x"))            # 模型临时题 id 不稳定
    for qid in ("C_FMT", "C_NULL", "C_SIGN"):                                                          # 新增列的格式题每条需求的列都不同
        assert not memorable(Question(qid, "展示格式", "通用", "高", "q?", "w", "d", answer="百分数、保留 2 位"))


def _q(qid: str, answer: str = "") -> Question:
    return Question(qid, "口径", "期货", "高", f"{qid} 按什么口径？", "w", "d", answer=answer)


def test_second_time_not_asked_again(mem):
    # 第一次：业务答了两道题 → 记住
    a1 = analyze(SAMPLE_1, memory=mem)
    assert a1.recalled == 0
    asked = [q for q in a1.questions]
    assert len(asked) >= 2
    q_a, q_b = asked[0], asked[1]
    answers = {q_a.id: "以上一交易日结算价为基准", q_b.id: "客户版隐去内部字段"}
    a1b = analyze(SAMPLE_1, answers=answers, memory=mem)
    n = mem.learn(a1b.card, a1b.questions, req_id=1)
    assert n == 2
    st = mem.stats()
    assert st["entries"] == 2 and st["requesters"] >= 1 and st["hits"] == 0

    # 第二次：同一提出方、同类需求 → 这两题直接沿用，不再问
    a2 = analyze(SAMPLE_2, memory=mem)
    recalled = {q.id: q for q in a2.questions if q.recalled}
    assert a2.recalled == len(recalled) >= 1
    for qid, q in recalled.items():
        assert q.answer == answers[qid]
        assert "资管运营" in q.recalled
    # 确认消息里不再当问题问，而是"沿用"
    msg = confirm_message(a2.card, a2.questions, SAMPLE_2)
    assert "沿用你们上次的口径" in msg
    for q in recalled.values():
        assert q.question not in msg.split("沿用你们上次的口径")[0]
    # 需求单里带沿用标记；命中计数增长
    assert "🔁沿用" in a2.spec.business_md or "沿用该提出方上次口径" in a2.spec.business_md
    assert mem.stats()["hits"] == len(recalled)


def test_this_time_answer_overrides_memory(mem):
    a1 = analyze(SAMPLE_1, memory=mem)
    qid = a1.questions[0].id
    a1b = analyze(SAMPLE_1, answers={qid: "旧口径"}, memory=mem)
    mem.learn(a1b.card, a1b.questions)
    a2 = analyze(SAMPLE_1, answers={qid: "新口径"}, memory=mem)
    q = next(q for q in a2.questions if q.id == qid)
    assert q.answer == "新口径" and not q.recalled       # 本次答复优先，不算沿用
    mem.learn(a2.card, a2.questions)
    assert mem.lookup("资管运营", qid)["answer"] == "新口径"   # 记忆随之更新


def test_dept_fallback_and_forget(mem):
    a = analyze("投资部·期现 王经理提的：做一个基差监控页面，盘中要能看，超过阈值提醒。", memory=mem)
    assert a.card.requester.startswith("投资部")
    qid = a.questions[0].id
    a2 = analyze(a.raw_text, answers={qid: "阈值按品种设"}, memory=mem)
    mem.learn(a2.card, a2.questions)
    # 同部门其它角色也能沿用（部门级）
    hit = mem.lookup("投资部", qid)
    assert hit and hit["answer"] == "阈值按品种设"
    # 清掉这个提出方
    assert mem.forget(scope_key=a2.card.requester) >= 1
    assert mem.lookup(a2.card.requester, qid) is None or mem.lookup("投资部", qid) is not None
    assert mem.forget() >= 0
    assert mem.stats()["entries"] == 0


def test_no_requester_no_memory(mem):
    a = analyze("做一个页面看每天的成交量，按品种排序，要能导出。", memory=mem)
    if not a.card.requester:
        qid = a.questions[0].id
        a2 = analyze(a.raw_text, answers={qid: "x"}, memory=mem)
        assert mem.learn(a2.card, a2.questions) == 0
        assert analyze(a.raw_text, memory=mem).recalled == 0


def test_answers_with_customer_info_are_not_stored(mem):
    card = Card(requester="资管部·投资经理", req_type="报表")
    n = mem.learn(card, [_q("T03", "按客户王建国（手机 13812345678）口径"), _q("T05", "以结算价为准")])
    assert n == 1 and mem.last_skipped == 1
    assert mem.lookup("资管部·投资经理", "T03") is None and mem.lookup("资管部", "T03") is None
    assert "13812345678" not in str(mem.rows()) and "王建国" not in str(mem.rows())
    assert mem.lookup("资管部·投资经理", "T05")["answer"] == "以结算价为准"
    assert mem.learn(card, [_q("T06", "按日终")]) == 1 and mem.last_skipped == 0   # 每次 learn 重新计数


def test_old_sensitive_records_are_not_recalled(mem):
    """老库里已存的含手机号口径：不再沿用到新需求。"""
    mem.conn.execute(
        "INSERT INTO answer_memory(scope,scope_key,qid,question,answer,req_type,ts,hits,req_id) "
        "VALUES('requester','资管部·投资经理','T03','q','有疑问打 13812345678','报表','2026-01-01T00:00:00',0,1)")
    mem.conn.commit()
    card = Card(requester="资管部·投资经理")
    mem.learn(card, [_q("T05", "以结算价为准")])
    qs = [_q("T03"), _q("T05")]
    assert mem.recall(card, qs) == 1
    assert qs[0].answer == "" and not qs[0].recalled
    assert qs[1].answer == "以结算价为准" and qs[1].recalled


def test_forget_requester_also_clears_dept_copies(mem):
    a = Card(requester="资管部·投资经理")
    mem.learn(a, [_q("T03", "按结算价"), _q("T05", "日终")])
    assert mem.stats() == {"entries": 2, "hits": 0, "requesters": 1}   # 部门级副本不算一个提出方
    assert mem.forget(scope_key="资管部·投资经理") == 4
    for who in ("资管部·投资经理", "资管部·运营"):
        qs = [_q("T03"), _q("T05")]
        assert mem.recall(Card(requester=who), qs) == 0                # 部门级副本也清掉了，一条都不沿用
    assert mem.stats()["requesters"] == 0 and mem.rows() == []
    # 同部门别人后来记下的部门口径不受牵连
    mem.learn(a, [_q("T03", "按结算价")])
    mem.learn(Card(requester="资管部·运营"), [_q("T03", "按收盘价")])
    mem.forget(scope_key="资管部·投资经理")
    assert mem.lookup("资管部·风控", "T03")["answer"] == "按收盘价"


def test_column_format_answers_are_not_reused(mem):
    """「百分数、保留 2 位」是样例①那一列的格式，不能沿用到别的需求的新列上。"""
    a1 = analyze(SAMPLE_1, memory=mem)
    fmt = {q.id for q in a1.questions} & {"C_FMT", "C_NULL", "C_SIGN"}
    assert "C_FMT" in fmt
    answers = {qid: "百分数、保留 2 位" for qid in fmt}
    answers[a1.questions[0].id] = "以上一交易日结算价为基准"
    a1b = analyze(SAMPLE_1, answers=answers, memory=mem)
    assert mem.learn(a1b.card, a1b.questions) == 1                     # 只记口径题
    a2 = analyze(SAMPLE_2, memory=mem)
    later = [q for q in a2.questions if q.id in fmt]
    assert later and all(not q.recalled and not q.answer for q in later)


def test_recall_can_skip_hit_counting(mem):
    card = Card(requester="资管运营")
    mem.learn(card, [_q("T03", "按结算价")])
    assert mem.recall(card, [_q("T03")], count_hits=False) == 1 and mem.stats()["hits"] == 0
    assert mem.recall(card, [_q("T03")]) == 1 and mem.stats()["hits"] == 1


def test_one_memory_shared_by_threads(mem):
    card = Card(requester="资管运营")
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(20):
                mem.learn(card, [_q(f"T{n}_{i}", f"口径{n}-{i}")])
                mem.recall(card, [_q(f"T{n}_{i}")])
        except Exception as e:   # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and mem.stats()["entries"] == 120 and mem.stats()["hits"] == 120

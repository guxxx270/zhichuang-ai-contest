"""后台模型任务：用事件控制调用，不依赖 sleep 或模型响应速度。"""
from __future__ import annotations

from copy import deepcopy
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from xuzhi import background, config, pipeline
from xuzhi.drafts import CodeFile, Draft, RepoBundle
from xuzhi.pipeline.estimate import Estimate
from xuzhi.pipeline.estimate_references import build_references
from xuzhi.pipeline.intake import Question


@pytest.fixture
def seed():
    return pipeline.analyze("现有集团报送需要修改一个指标的计算方法：权益敞口。", llm_polish=False)


def _model(**extra):
    return SimpleNamespace(mode="api", model="unit-test", **extra)


def _ready(value=1):
    return Estimate(status="ready", method="llm", mid=value, ai_mid=value / 2)


def test_start_returns_while_model_is_blocked_and_captures_input(monkeypatch, seed):
    entered, release, returned = Event(), Event(), Event()
    observed, holder = {}, {}
    original = deepcopy(seed)

    def blocked(card, text, questions, **kwargs):
        observed["title"] = card.title
        entered.set()
        assert release.wait(5), "测试没有放行模型调用"
        card.features.append("仅后台副本可见")
        return _ready()

    monkeypatch.setattr(background, "estimate", blocked)

    def launch():
        holder["job"] = background.start_estimate_job(seed, _model())
        returned.set()

    launcher = Thread(target=launch, daemon=True)
    launcher.start()
    try:
        assert entered.wait(2)
        assert returned.wait(2), "模型调用未结束时启动函数必须已经返回"
        job = holder["job"]
        assert job.snapshot()["status"] == "running" and job.snapshot()["stage"] == "模型估时"
        assert job.snapshot()["result"] is None and not job.done.is_set()
        seed.card.title = "启动后改写当前页面需求"
        assert observed["title"] == original.card.title
    finally:
        release.set()
        launcher.join(2)
    assert job.wait(2)
    snapshot = job.snapshot()
    assert snapshot["status"] == "completed" and snapshot["result"].mid == 1
    assert seed.card.features == original.card.features
    assert job._thread.daemon
    snapshot["result"].mid = 999
    assert job.snapshot()["result"].mid == 1, "外部读取结果不能改写任务内部状态"


def test_full_enhancement_reports_each_stage_and_preserves_answers_materials_view(monkeypatch, seed):
    seed.questions[0].answer = "沿用已确认字段"
    seed.questions[0].recalled = "历史口径 · 2026-10-19"
    seed.recalled = 1
    seed.mobile = seed.client_view = True
    seed.drafts = [Draft("page.html", "upload", "<p>原底稿</p>")]
    seed.repos = [RepoBundle("local-demo", files=[CodeFile("report.py", "backend", "old_value = 1")])]
    original = deepcopy(seed)
    names = ["模型问清", "模型写单", "模型估时", "模型出样"]
    gates = {name: (Event(), Event()) for name in names}
    calls, views = [], []

    def gate(name):
        calls.append(name)
        gates[name][0].set()
        assert gates[name][1].wait(5), "测试没有放行阶段"

    def clarify(text, card, questions, llm, **materials):
        assert materials["drafts"] == original.drafts and materials["repos"] == original.repos
        assert materials["drafts"] is not seed.drafts and materials["repos"] is not seed.repos
        gate("模型问清")
        card.title = "增强后的需求标题"
        card.engine = "规则 + 模型"
        # 模拟模型因材料过滤掉旧问题；已确认的口径仍须带到后续步骤。
        return card, [Question("NEW", "范围", "模型", "中", "新问题？", "范围确认", "待确认")]

    def spec(card, questions, coverage, llm):
        gate("模型写单")
        assert any(question.answer == "沿用已确认字段" and question.recalled for question in questions)
        result = deepcopy(seed.spec)
        result.engine = "规则 + 模型"
        return result

    def estimate(card, text, questions, **kwargs):
        gate("模型估时")
        assert kwargs["repos"] == original.repos
        return _ready(2)

    def source(card, **kwargs):
        gate("模型出样")
        return "<p>增强后的页面</p>"

    monkeypatch.setattr(pipeline, "refine_with_llm", clarify)
    monkeypatch.setattr(pipeline, "build_architecture", lambda *args, **kwargs: deepcopy(seed.architecture))
    monkeypatch.setattr(pipeline, "build_spec", spec)
    monkeypatch.setattr(pipeline, "estimate", estimate)
    monkeypatch.setattr(pipeline, "render_source", source)
    monkeypatch.setattr(pipeline, "apply_prototype_view", lambda html, **kwargs: views.append(kwargs) or html)
    job = background.start_analysis_job(seed, _model())
    try:
        for name in names:
            assert gates[name][0].wait(2)
            assert job.snapshot()["stage"] == name and job.snapshot()["status"] == "running"
            gates[name][1].set()
        assert job.wait(2)
    finally:
        for _, release in gates.values():
            release.set()
    result = job.snapshot()["result"]
    assert job.snapshot()["status"] == "completed"
    assert calls == names and result.card.title == "增强后的需求标题"
    assert result.recalled == 1 and result.questions[-1].answer == "沿用已确认字段"
    assert result.drafts == original.drafts and result.repos == original.repos
    assert views == [{"mobile": True, "client_view": True}]
    assert seed == original, "模型增强不得改写当前快速结果"


def test_uncaught_failure_is_safe_and_does_not_modify_seed(monkeypatch, seed):
    original = deepcopy(seed)

    def fail(*args, **kwargs):
        raise RuntimeError("Authorization: Bearer sk-secret-test https://gateway.example?key=secret")

    monkeypatch.setattr(background, "estimate", fail)
    job = background.start_estimate_job(seed, _model())
    assert job.wait(2)
    snapshot = job.snapshot()
    assert snapshot["status"] == "failed" and snapshot["result"] is None
    assert "快速结果已保留" in snapshot["error"]
    assert "secret" not in snapshot["error"] and "gateway" not in snapshot["error"]
    assert seed == original


def test_estimate_only_calls_once_and_returns_handled_model_failure(monkeypatch, seed):
    failed = Estimate(status="failed", error="网关未返回有效估时")
    estimate = Mock(return_value=failed)
    monkeypatch.setattr(background, "estimate", estimate)
    job = background.start_estimate_job(seed, _model())

    assert job.wait(2)
    snapshot = job.snapshot()
    assert estimate.call_count == 1
    assert snapshot["status"] == "completed" and snapshot["result"].status == "failed"
    assert snapshot["result"].mid is None


def test_independent_jobs_can_finish_out_of_order(monkeypatch, seed):
    gates = {name: (Event(), Event()) for name in ("first", "second")}

    def estimate(card, text, questions, *, llm, **kwargs):
        entered, release = gates[llm.name]
        entered.set()
        assert release.wait(5)
        return _ready(1 if llm.name == "first" else 2)

    monkeypatch.setattr(background, "estimate", estimate)
    first = background.start_estimate_job(seed, _model(name="first"))
    second = background.start_estimate_job(seed, _model(name="second"))
    try:
        assert gates["first"][0].wait(2) and gates["second"][0].wait(2)
        gates["second"][1].set()
        assert second.wait(2)
        assert second.snapshot()["result"].mid == 2 and first.snapshot()["status"] == "running"
        gates["first"][1].set()
        assert first.wait(2)
        assert first.snapshot()["result"].mid == 1 and first.id != second.id
    finally:
        for _, release in gates.values():
            release.set()


def test_discard_does_not_interrupt_active_call_but_skips_next_stage(monkeypatch, seed):
    entered, release = Event(), Event()

    def clarify(text, card, questions, llm, **kwargs):
        entered.set()
        assert release.wait(5)
        return card, questions

    next_stage = Mock(side_effect=AssertionError("丢弃的任务不得调用后续阶段"))
    monkeypatch.setattr(pipeline, "refine_with_llm", clarify)
    monkeypatch.setattr(pipeline, "build_spec", next_stage)
    job = background.start_analysis_job(seed, _model())
    try:
        assert entered.wait(2)
        job.discard()
        assert job.snapshot()["discarded"] and not job.done.is_set()
    finally:
        release.set()
    assert job.wait(2)
    assert next_stage.call_count == 0 and job.snapshot()["result"] is None
    assert job.snapshot()["stage"] == "已忽略旧任务"


@pytest.mark.parametrize("owned", [True, False])
def test_only_owned_audit_connection_is_closed(monkeypatch, seed, owned):
    close = Mock()
    llm = _model(audit=SimpleNamespace(conn=SimpleNamespace(close=close)), _job_owned_audit=owned)
    monkeypatch.setattr(background, "estimate", Mock(side_effect=TimeoutError("secret")))
    job = background.start_estimate_job(seed, llm)

    assert job.wait(2)
    assert close.call_count == int(owned)
    assert "超时" in job.snapshot()["error"] and "secret" not in job.snapshot()["error"]


def test_rule_reference_is_not_rewritten_by_model_clarify(monkeypatch):
    """规则模板参考独立于模型：模型问清改了功能点、补了高影响题，后台跑完规则参考仍是快速结果那份（模型估时失败也一样）。"""
    text = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")
    seed = pipeline.analyze(text, llm_polish=False)

    def clarify(text, card, questions, llm, **materials):
        card.features = [f"功能点{i}" for i in range(1, 7)]
        card.engine = "规则 + 模型"
        extra = [Question(f"L{i}", "补充", "模型", "高", f"模型补问{i}？", "w", "待业务答复") for i in (1, 2)]
        return card, list(questions) + extra

    monkeypatch.setattr(pipeline, "refine_with_llm", clarify)
    result = pipeline.enhance_analysis(seed, _model())   # 假模型没有 chat：写单、估时、出样都按各自的失败回退
    assert result.estimate.status == "failed"
    assert result.estimate.references["rule"] == seed.estimate.references["rule"]
    assert result.estimate.references == seed.estimate.references
    # 这组改写确实会让重算的规则数字变化，上面相等不是巧合
    assert build_references(result.card, result.redacted_text, result.questions)["rule"] != seed.estimate.references["rule"]


def test_prototype_failure_keeps_finished_estimate(monkeypatch, seed):
    monkeypatch.setattr(pipeline, "refine_with_llm", lambda text, card, questions, llm, **kw: (card, questions))
    monkeypatch.setattr(pipeline, "estimate", lambda *args, **kwargs: _ready(2))

    def broken(*args, **kwargs):
        raise KeyError("layout")

    monkeypatch.setattr(pipeline, "render_source", broken)
    job = background.start_analysis_job(seed, _model())
    assert job.wait(2)
    snapshot = job.snapshot()
    assert snapshot["status"] == "completed"
    result = snapshot["result"]
    assert result.estimate.status == "ready" and result.estimate.mid == 2
    assert result.prototype_html == seed.prototype_html and result.prototype_source_html == seed.prototype_source_html
    assert "模型出样未完成，已保留规则原型" in result.notes


def test_discard_during_estimate_still_skips_prototype(monkeypatch, seed):
    """出样那段的保护不能吞掉「任务已丢弃」：丢弃后不再出样，也不发布结果。"""
    entered, release = Event(), Event()

    def estimate(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return _ready(1)

    render = Mock(side_effect=AssertionError("丢弃的任务不得再出样"))
    monkeypatch.setattr(pipeline, "refine_with_llm", lambda text, card, questions, llm, **kw: (card, questions))
    monkeypatch.setattr(pipeline, "estimate", estimate)
    monkeypatch.setattr(pipeline, "render_source", render)
    job = background.start_analysis_job(seed, _model())
    try:
        assert entered.wait(2)
        job.discard()
    finally:
        release.set()
    assert job.wait(2)
    assert render.call_count == 0 and job.snapshot()["result"] is None
    assert job.snapshot()["stage"] == "已忽略旧任务"

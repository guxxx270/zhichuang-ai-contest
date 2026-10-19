"""模型依据需求和代码分别估人工/AI工时；程序仅校验证据与汇总。"""
from __future__ import annotations

import copy
import json

import pytest

from test_intake_requirements import EQUITY_REQUIREMENT
from xuzhi import knowledge
from xuzhi.drafts import CodeFile, RepoBundle
from xuzhi.pipeline.estimate import estimate
from xuzhi.pipeline.intake import Question, extract_card


REQUIREMENT_QUOTE = "需要修改一个指标的计算方法：权益敞口"
CODE_QUOTE = 'return row["old_beta_value"]'
CODE = 'def equity_exposure(row):\n    return row["old_beta_value"]\n'
TOTAL_FIELDS = ("mid", "low", "high", "ai_mid", "ai_low", "ai_high")


class FakeLLM:
    mode = "api"
    model = "test-estimator"
    last_model = "test-estimator"
    current_purpose = ""

    def __init__(self, response=None, *, error=None):
        self.response = response if response is not None else _response()
        self.error = error
        self.calls = []

    def chat_json(self, system, user):
        assert self.current_purpose == "估时分析"
        payload = json.loads(user)
        self.calls.append({"system": system, "user": user, "payload": payload})
        if self.error:
            raise self.error
        answer = self.response(payload) if callable(self.response) else self.response
        return copy.deepcopy(answer)


def _response():
    def task(name, phase, who, before, after):
        return {
            "name": name,
            "phase": phase,
            "who": who,
            "change": "根据现有计算规则修改字段取值，并核对权益敞口输出。",
            "before": dict(zip(("low", "mid", "high"), before), reason="人工定位、实现和检查这项工作。"),
            "after": dict(zip(("low", "mid", "high"), after), reason="结合代码生成、审查和人工核验分别估计。"),
            "evidence": [{"source": "requirement", "file": "", "quote": REQUIREMENT_QUOTE}],
        }

    return {
        "summary": "修改既有计算，并逐项核对关键数字。",
        "confidence": "高",
        "confidence_reason": "模型声明已理解需求；程序仍须按实际证据限制置信度。",
        "assumptions": ["字段映射尚需业务确认。"],
        "tasks": [
            task("核对字段映射", "需求与设计", "AI 做人审", (1, 2, 3), (0.5, 1, 1.5)),
            task("修改计算与回归样本", "开发", "AI 做人审", (2, 5, 9), (1, 2, 4)),
            task("人工核对关键数字", "测试与比对", "人必须做", (1, 2, 3), (1, 3, 5)),
        ],
    }


def _repo(*files):
    files = list(files) or [CodeFile("src/exposure.py", "backend", CODE)]
    return RepoBundle("https://example.com/team/demo.git", tree=[f.path for f in files], files=files)


def _estimate(llm=None, *, text=EQUITY_REQUIREMENT, questions=None, repos=None):
    return estimate(extract_card(text), text, questions or [], llm=llm, repos=repos)


def _question(answer):
    return Question("Q-scope", "范围", "通用", "高", "实际字段是什么？", "确定修改位置", "待确认", answer=answer)


def _assert_unestimated(result, status):
    assert result.status == status
    assert result.tasks == []
    assert all(getattr(result, field) is None for field in TOTAL_FIELDS), "失败或不可用时不能冒出模板工时"


def _code_response(payload):
    response = _response()
    code_file = next(f for f in payload["code_files"] if CODE_QUOTE in f["excerpt"])
    response["tasks"][1]["evidence"].append({"source": "code", "file": code_file["id"], "quote": CODE_QUOTE})
    return response


def test_model_estimates_each_track_and_program_sums_tasks_instead_of_model_totals():
    response = _response()
    response["total"] = {"before": 9997, "after": 9998}
    response["mid"] = 9999
    llm = FakeLLM(response)

    result = _estimate(llm)

    assert result.status == "ready" and result.method == "llm"
    assert result.model == llm.model
    assert len(llm.calls) == 1
    assert result.summary == response["summary"]
    assert set(response["assumptions"]) <= set(result.assumptions)
    assert (result.low * 8, result.mid * 8, result.high * 8) == pytest.approx((4, 9, 15))
    assert (result.ai_low * 8, result.ai_mid * 8, result.ai_high * 8) == pytest.approx((2.5, 6, 10.5))
    assert [task.trad_days * 8 for task in result.tasks] == pytest.approx([2, 5, 2])
    assert [task.ai_days * 8 for task in result.tasks] == pytest.approx([1, 2, 3])
    assert sum(result.phases.values()) == pytest.approx(result.mid)
    assert sum(result.ai_phases.values()) == pytest.approx(result.ai_mid)
    for phase in result.phases:
        tasks = [task for task in result.tasks if task.phase == phase]
        assert result.phases[phase] == pytest.approx(sum(task.trad_days for task in tasks))
        assert result.ai_phases[phase] == pytest.approx(sum(task.ai_days for task in tasks))


def test_ai_work_may_cost_more_and_is_not_forced_below_manual_work():
    response = _response()
    for task in response["tasks"]:
        before = task["before"]
        task["after"].update(low=before["low"] + 1, mid=before["mid"] + 2, high=before["high"] + 4)

    result = _estimate(FakeLLM(response))

    assert result.status == "ready"
    assert result.ai_mid * 8 == pytest.approx(15)
    assert result.ai_mid > result.mid
    assert result.saving_pct < 0
    assert all(task.ai_days > task.trad_days for task in result.tasks)


@pytest.mark.parametrize("mock_model", [None, "mock"], ids=["absent", "offline"])
def test_no_available_model_leaves_totals_unestimated(mock_model):
    llm = FakeLLM() if mock_model else None
    if llm:
        llm.mode = mock_model
    result = _estimate(llm)

    _assert_unestimated(result, "unavailable")
    if llm:
        assert llm.calls == []


def test_model_exception_has_no_partial_or_fixed_estimate():
    result = _estimate(FakeLLM(error=RuntimeError("gateway unavailable")))
    _assert_unestimated(result, "failed")


def test_missing_model_channel_has_an_actionable_safe_error():
    error = RuntimeError("secret-key-must-not-appear")
    error.body = {"error": {"code": "model_not_found", "message": "untrusted gateway details"}}
    result = _estimate(FakeLLM(error=error))

    _assert_unestimated(result, "failed")
    assert "可用渠道" in result.error and "可用模型目录" in result.error
    assert "secret-key" not in result.error and "untrusted" not in result.error
    assert result.references["rule"]["status"] == "ready"


@pytest.mark.parametrize(
    ("track", "field", "bad"),
    [
        ("before", "low", True),
        ("after", "mid", False),
        ("before", "low", float("nan")),
        ("after", "high", float("inf")),
        ("before", "high", float("-inf")),
        ("after", "mid", "2"),
        ("before", "low", -1),
        ("after", "low", 20),
        ("before", "high", 1),
        ("after", "mid", 0),
        ("before", "mid", None),
    ],
    ids=["bool-low", "bool-mid", "nan", "infinity", "negative-infinity", "numeric-string", "negative", "low-above-mid", "high-below-mid", "zero-mid", "null"],
)
def test_invalid_task_hours_reject_the_whole_model_result(track, field, bad):
    response = _response()
    response["tasks"][1][track][field] = bad

    _assert_unestimated(_estimate(FakeLLM(response)), "failed")


@pytest.mark.parametrize("before,after", [(5e-324, 5e-324), (1e-310, 100000), (1e-304, 1000)])
def test_conversion_underflow_or_comparison_overflow_clears_the_whole_result(before, after):
    response = _response()
    for task in response["tasks"]:
        task["before"].update(low=before, mid=before, high=before)
        task["after"].update(low=after, mid=after, high=after)

    result = _estimate(FakeLLM(response))

    _assert_unestimated(result, "failed")
    assert result.saving_pct is None
    assert result.phases == result.ai_phases == result.who == {}


def test_small_representable_values_do_not_get_a_fixed_minimum_duration():
    response = _response()
    for task in response["tasks"]:
        task["before"].update(low=0, mid=1e-10, high=1e-9)
        task["after"].update(low=0, mid=1e-10, high=1e-9)

    result = _estimate(FakeLLM(response))

    assert result.status == "ready"
    assert result.mid > 0
    assert result.mid * 8 == pytest.approx(3e-10, abs=1e-25)
    assert result.saving_pct == 0


@pytest.mark.parametrize("bad_response", [{}, {"tasks": []}, [], "not-json"])
def test_incomplete_or_nonobject_model_response_is_rejected(bad_response):
    _assert_unestimated(_estimate(FakeLLM(bad_response)), "failed")


def test_requirement_only_estimate_is_labeled_rough_and_low_confidence():
    result = _estimate(FakeLLM())

    assert result.status == "ready"
    assert result.basis == "requirements_only"
    assert result.confidence == "低", "模型自报高置信不能替代代码核对"
    assert not result.code_files
    assert any("代码" in line for line in result.assumptions + [result.confidence_reason])


def test_sent_code_with_a_real_quote_can_support_code_excerpt_basis():
    llm = FakeLLM(_code_response)
    result = _estimate(llm, repos=[_repo()])

    assert result.status == "ready" and result.basis == "code_excerpt"
    assert result.code_files
    sent = llm.calls[0]["payload"]["code_files"]
    assert any(f["path"] == "src/exposure.py" and CODE_QUOTE in f["excerpt"] for f in sent)
    assert len({f["id"] for f in sent}) == len(sent)


def test_uploaded_code_without_a_real_code_citation_remains_requirement_only():
    result = _estimate(FakeLLM(), repos=[_repo()])

    assert result.status == "ready"
    assert result.basis == "requirements_only"
    assert result.confidence == "低"


@pytest.mark.parametrize("bad_evidence", ["unknown-file", "invented-code", "invented-requirement", "invented-answer"])
def test_fabricated_evidence_rejects_entire_estimate(bad_evidence):
    def response(payload):
        model = _response()
        evidence = {"source": "requirement", "file": "", "quote": REQUIREMENT_QUOTE}
        if bad_evidence == "unknown-file":
            evidence.update(source="code", file="R999/missing.py", quote=CODE_QUOTE)
        elif bad_evidence == "invented-code":
            evidence.update(source="code", file=payload["code_files"][0]["id"], quote="return invented_calculation()")
        elif bad_evidence == "invented-requirement":
            evidence["quote"] = "需求明确要求本周重建全部生产系统"
        else:
            evidence.update(source="answer", quote="业务已同意新增外部交易网关")
        model["tasks"][1]["evidence"] = [evidence]
        return model

    _assert_unestimated(_estimate(FakeLLM(response), repos=[_repo()]), "failed")


def test_answer_evidence_is_checked_against_the_answers_sent_to_model():
    answer = "字段就是现有的费前市值，不再乘一次指数价格比。"
    response = _response()
    response["tasks"][0]["evidence"] = [{"source": "answer", "file": "", "quote": answer}]
    llm = FakeLLM(response)

    result = _estimate(llm, questions=[_question(answer)])

    assert result.status == "ready"
    assert answer in json.dumps(llm.calls[0]["payload"]["answers"], ensure_ascii=False)


def test_inputs_are_redacted_and_credential_files_never_enter_the_prompt():
    secrets = {
        ".env.production": "RAW_DOTENV_MARKER",
        "config/credentials.json": "RAW_CREDENTIAL_MARKER",
        "config/secrets.yaml": "RAW_SECRET_FILE_MARKER",
        "keys/server.key": "RAW_PRIVATE_KEY_MARKER",
        ".ssh/id_rsa": "RAW_SSH_KEY_MARKER",
    }
    code = CODE + '# 电话 13912345678\napi_key = "sk-private-demo-key"\n'
    files = [CodeFile("src/exposure.py", "backend", code)]
    files += [CodeFile(path, "config", value) for path, value in secrets.items()]
    llm = FakeLLM(_code_response)
    result = _estimate(
        llm,
        text=EQUITY_REQUIREMENT + "\n联系号码13912345678。",
        questions=[_question("联系确认邮箱 reviewer@example.com")],
        repos=[_repo(*files)],
    )

    assert result.status == "ready"
    call = llm.calls[0]
    sent = call["user"]
    assert "13912345678" not in sent and "reviewer@example.com" not in sent
    assert "sk-private-demo-key" not in sent
    assert all(marker not in sent for marker in secrets.values())
    assert not ({f["path"] for f in call["payload"]["code_files"]} & set(secrets))
    assert any(CODE_QUOTE in f["excerpt"] for f in call["payload"]["code_files"])


def test_model_prompt_receives_no_historical_hours_or_fixed_estimation_table(monkeypatch):
    history = knowledge.HistoryItem(
        "unrelated-history", 2025, "HISTORY_SENTINEL_PROJECT", "报表", "技术部",
        ("权益敞口",), 9843.125, 9844.375, "历史大项目不能拿来套小改",
    )
    monkeypatch.setattr(knowledge, "history", lambda: [history])
    llm = FakeLLM()

    result = _estimate(llm)

    assert result.status == "ready"
    sent = llm.calls[0]["system"] + llm.calls[0]["user"]
    assert all(marker not in sent for marker in ("HISTORY_SENTINEL_PROJECT", "9843.125", "9844.375"))
    assert not ({"history", "history_days", "similar", "fixed_hours", "coefficients"} & set(llm.calls[0]["payload"]))
    assert result.mid * 8 == pytest.approx(9), "结果必须来自本次模型分解，而非旧的固定15小时工作包"

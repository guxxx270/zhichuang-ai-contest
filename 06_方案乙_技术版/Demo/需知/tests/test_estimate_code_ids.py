"""不同路径脱敏为相同标签时，两份真实代码证据仍应能独立引用。"""
from __future__ import annotations

import pytest

from test_llm_estimate import FakeLLM, _estimate, _repo, _response
from xuzhi.drafts import CodeFile


@pytest.mark.parametrize(
    "paths",
    [
        ("reports/13812345678.py", "reports/13912345678.py"),
        ("products/123456789012/report.py", "products/987654321098/report.py"),
    ],
    ids=["phone-paths", "account-paths"],
)
def test_distinct_files_with_identical_redacted_paths_remain_valid_evidence(paths):
    quotes = ('return row["own_beta_value"]', 'return row["parent_beta_value"]')
    repo = _repo(*[
        CodeFile(path, "backend", f"def equity_exposure(row):\n    {quote}\n")
        for path, quote in zip(paths, quotes)
    ])

    def response(payload):
        answer = _response()
        for task, quote in zip(answer["tasks"], quotes):
            source = next(item for item in payload["code_files"] if quote in item["excerpt"])
            task["evidence"] = [{"source": "code", "file": source["id"], "quote": quote}]
        return answer

    llm = FakeLLM(response)
    result = _estimate(llm, repos=[repo])

    assert result.status == "ready", result.error
    sent = llm.calls[0]["payload"]["code_files"]
    assert len(sent) == 2
    assert sent[0]["path"] == sent[1]["path"], "该回归必须覆盖脱敏路径碰撞"
    assert len({item["id"] for item in sent}) == 2
    assert all(path not in llm.calls[0]["user"] for path in paths), "唯一性不能依赖泄露原路径"
    checked = [task.evidence[0] for task in result.tasks[:2]]
    assert len({item["file"] for item in checked}) == 2
    sent_by_id = {item["id"]: item["excerpt"] for item in sent}
    assert all(item["quote"] in sent_by_id[item["file"]] for item in checked)

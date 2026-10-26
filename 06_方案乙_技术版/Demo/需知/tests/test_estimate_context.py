"""估时根据已提供的源码全文取证，不沿用出样的文件头与 UI 优先排序。"""
from __future__ import annotations

import json

import pytest

from test_llm_estimate import FakeLLM, _estimate, _response
from xuzhi.drafts import CodeFile, RepoBundle
from xuzhi.pipeline.estimate_context import estimate_code_files


PADDING = "def unrelated_helper(value):\n    return value + 1\n\n" * 220
TAIL_QUOTE = 'return row["w52_beta_pnl"]'


def _repo(*files):
    return RepoBundle("https://example.invalid/demo.git", files=list(files))


def _tail_file(path="reports/export.py"):
    return CodeFile(path, "backend", "import math\n\n" + PADDING + "# 权益敞口\ndef exposure(row):\n    " + TAIL_QUOTE + "\n")


def test_tail_function_and_header_are_selected_and_real_quote_remains_valid():
    source = _tail_file()
    assert source.text.index(TAIL_QUOTE) > 4000

    def response(payload):
        answer = _response()
        code = next(item for item in payload["code_files"] if TAIL_QUOTE in item["excerpt"])
        answer["tasks"][1]["evidence"] = [{"source": "code", "file": code["id"], "quote": TAIL_QUOTE}]
        return answer

    llm = FakeLLM(response)
    result = _estimate(llm, repos=[_repo(source)])

    assert result.status == "ready", result.error
    assert result.basis == "code_excerpt"
    sent = llm.calls[0]["payload"]["code_files"]
    assert "import math" in sent[0]["excerpt"]
    assert TAIL_QUOTE in sent[0]["excerpt"]
    assert len(sent[0]["excerpt"]) < len(source.text)
    assert result.tasks[1].evidence[0]["quote"] in sent[0]["excerpt"]


def test_identifiers_touching_chinese_and_period_unit_variant_find_implementation():
    source = _tail_file()
    result = estimate_code_files([_repo(source)], "权益敞口改取该产品52W_beta市值", code_limit=1800)
    assert TAIL_QUOTE in result[0]["excerpt"], "中文没有单词边界，仍须检出标识及 w52/52W 写法"


def test_answer_fields_drive_retrieval_even_when_absent_from_original_requirement():
    target = CodeFile("service/report.py", "backend", PADDING + 'def amount(row):\n    return row["t_pre_fee_pnl"]\n')
    noise = CodeFile("service/overview.py", "backend", "# 修改指标\n" + PADDING)
    result = estimate_code_files(
        [_repo(noise, target)], "修改现有报送指标", ["使用已有t_pre_fee_pnl字段"], code_limit=1500,
    )
    assert result[0]["path"] == target.path
    assert 'row["t_pre_fee_pnl"]' in result[0]["excerpt"]


def test_repeated_configuration_and_ui_labels_do_not_displace_exact_implementation():
    files = [CodeFile(f"config/settings{i}.json", "config", '{"52W_beta": "权益敞口、净值、产品"}\n' * 300) for i in range(12)]
    files.append(CodeFile("ui/overview.vue", "frontend", "<template><div>权益敞口 净值 产品</div></template>\n" * 300))
    files.append(_tail_file())
    result = estimate_code_files(
        [_repo(*files)], "将权益敞口改取52W_beta市值", keywords={"产品", "净值"}, indicators={"权益敞口"}, code_limit=2200,
    )
    assert result[0]["path"] == "reports/export.py"
    assert TAIL_QUOTE in result[0]["excerpt"]


def test_explicitly_named_configuration_is_still_eligible():
    config = CodeFile("config/limits.yaml", "config", 'risk_limit: 100\n')
    code = CodeFile("service/risk.py", "backend", 'risk_limit = read_limit()\n')
    result = estimate_code_files([_repo(code, config)], "修改limits.yaml中的risk_limit", code_limit=500)
    assert result[0]["path"] == config.path


def test_global_ranking_reaches_later_repository_and_uses_original_unique_indexes():
    repos = [_repo(CodeFile(f"misc/{i}.py", "backend", PADDING)) for i in range(3)]
    repos.append(_repo(_tail_file()))
    result = estimate_code_files(repos, "更新52W_beta权益敞口", code_limit=1600)
    assert result[0]["id"].startswith("R4/F1/")
    assert TAIL_QUOTE in result[0]["excerpt"]
    assert len({item["id"] for item in result}) == len(result)


def test_overlapping_windows_are_merged_without_duplicating_function():
    function = 'def update_metric(row):\n    beta_a = row["beta_a"]\n    beta_b = row["beta_b"]\n    return beta_a + beta_b\n'
    source = "import math\n" + PADDING + function + PADDING
    result = estimate_code_files([_repo(CodeFile("service/calc.py", "backend", source))], "修改beta_a和beta_b", code_limit=4500)
    excerpt = result[0]["excerpt"]
    assert function in excerpt
    assert excerpt.count("def update_metric") == 1
    assert excerpt.count("import math") == 1


@pytest.mark.parametrize("budget", [0, 80, 256, 800, 2800, 28000])
def test_total_json_budget_includes_paths_separators_and_escaped_code(budget):
    files = [CodeFile(f"service/part_{i}.py", "backend", 'beta_value = "\\quoted\\"\n' * 1000) for i in range(10)]
    result = estimate_code_files([_repo(*files)], "修改beta_value", code_limit=budget)
    if budget <= 2:
        assert result == []
    else:
        assert len(json.dumps(result, ensure_ascii=False)) <= budget
    assert all(item["excerpt"].strip() for item in result)


def test_secret_files_are_skipped_and_text_is_redacted_before_window_truncation():
    secret = "synthetic-credential-value-that-must-not-appear"
    source = CodeFile(
        "reports/13912345678.py", "backend",
        PADDING + f'api_key = "{secret}"\n# 联系 13812345678\n' + TAIL_QUOTE + "\n",
    )
    credential = CodeFile("config/credentials.json", "config", 'RAW_CREDENTIAL_FILE beta w52_beta_pnl')
    result = estimate_code_files([_repo(credential, source)], "修改52W_beta", code_limit=1500)
    sent = json.dumps(result, ensure_ascii=False)
    assert "RAW_CREDENTIAL_FILE" not in sent
    assert secret not in sent
    assert "13912345678" not in sent and "13812345678" not in sent
    assert TAIL_QUOTE in sent.replace('\\"', '"')
    assert result[0]["id"].startswith("R1/F2/")


def test_business_entry_and_calculation_survive_many_upstream_technical_matches():
    endpoint = CodeFile("api/report.py", "backend", "import json\n" + PADDING + '# 集团报送权益敞口\ndef send_report():\n    return load_exposure()\n')
    calculation = CodeFile("service/amount.py", "backend", "import math\n" + PADDING + '# 权益敞口\ndef calculate(row):\n    return row["w52_beta_pnl"]\n')
    upstream = [CodeFile(f"service/upstream_{i}.py", "backend", '# 净值 对标指数 穿透产品 费前市值\n' + 'beta = w52_beta_pnl + w52_beta + beta_coefficient\n' + PADDING) for i in range(20)]
    text = "给集团报送的数据中，需要修改权益敞口。权益敞口改取52W_beta市值，权益敞口按beta_coefficient计算；使用净值、对标指数、穿透产品、费前市值。"
    result = estimate_code_files([_repo(*upstream, endpoint, calculation)], text, indicators=["权益敞口", "净值", "对标指数"], code_limit=20000)
    by_path = {item["path"]: item["excerpt"] for item in result}
    assert endpoint.path in by_path and calculation.path in by_path
    assert "def send_report" in by_path[endpoint.path], "业务入口尾部必须真正进入摘录，而不只是文件名被选中"
    assert 'row["w52_beta_pnl"]' in by_path[calculation.path]


def test_raw_chinese_business_phrase_works_without_card_or_project_specific_words():
    endpoint = CodeFile("api/export.py", "backend", PADDING + '# 监管报送账户余额\ndef export():\n    return balances()\n')
    code = CodeFile("service/balance.py", "backend", PADDING + '# 账户余额\ncurrent_balance = load_balance()\n')
    noise = [CodeFile(f"service/source{i}.py", "backend", "current_balance = load_balance()\n" + PADDING) for i in range(12)]
    result = estimate_code_files([_repo(*noise, endpoint, code)], "给监管报送的数据中，需要修改账户余额。账户余额改取current_balance。", code_limit=18000)
    by_path = {item["path"]: item["excerpt"] for item in result}
    assert "def export" in by_path[endpoint.path]
    assert "current_balance" in by_path[code.path]


def test_copied_tests_and_archives_do_not_take_business_implementation_slots():
    copied = '# 集团报送 权益敞口 对标指数 费前市值\nbeta = w52_beta + beta_coefficient\n'
    noise = [CodeFile(f"{folder}/part_{i}.py", "backend", copied + PADDING) for folder in ("tests", "outputs", "archive") for i in range(3)]
    noise += [CodeFile("settings/catalog.json", "config", copied + PADDING)]
    endpoint = CodeFile("api/exposure.py", "backend", PADDING + '# 集团报送权益敞口\ndef report():\n    return exposure()\n')
    calc = CodeFile("service/metric.py", "backend", PADDING + '# 权益敞口\nvalue = w52_beta\n')
    result = estimate_code_files([_repo(*noise, endpoint, calc)], "集团报送需要修改权益敞口。权益敞口按52W_beta和beta_coefficient计算。", indicators=["权益敞口", "对标指数"], code_limit=13500)
    assert {item["path"] for item in result[:2]} == {endpoint.path, calc.path}
    assert all(len(item["excerpt"]) >= 100 for item in result), "预算尾部不能新增只有几个字符的源码证据"

"""命令行与网页：批量脱敏共用映射表、还原、check 的退出码；页面走一遍示例 → 脱敏 → 还原。"""
import json
from pathlib import Path

import pytest

import cli

ROOT = Path(__file__).resolve().parents[1]


def test_cli_batch_redact_restore_and_check(tmp_path, capsys):
    src = tmp_path / "材料"
    src.mkdir()
    (src / "a.txt").write_text("客户王建国先生，手机 13912345678", encoding="utf-8")
    (src / "b.md").write_text("王建国先生的资金账号：88123456；手机 139-1234-5678", encoding="utf-8")
    out = tmp_path / "out"
    assert cli.main(["redact", str(src), "--out", str(out)]) == 0
    a, b = (out / "a.txt").read_text(encoding="utf-8"), (out / "b.md").read_text(encoding="utf-8")
    assert "<客户_1>" in a and "<客户_1>" in b and "<手机_1>" in b and "13912345678" not in a + b   # 两份材料共用一张表
    mapping = json.loads((out / "隐盾映射表.map.json").read_text(encoding="utf-8"))
    assert mapping["<客户_1>"] == "王建国"

    ans = tmp_path / "ans.txt"
    ans.write_text("请回电〈客户_1〉：<手机_1>", encoding="utf-8")
    assert cli.main(["restore", str(ans), str(out / "隐盾映射表.map.json")]) == 0
    assert "请回电王建国：13912345678" in capsys.readouterr().out

    assert cli.main(["check", str(src)]) == 1          # 明文：退出码 1
    assert cli.main(["check", str(out / "a.txt")]) == 0   # 已脱敏：0


def test_cli_off_and_unknown_kind(tmp_path, capsys):
    f = tmp_path / "x.txt"
    f.write_text("浮亏 125 万元，手机 13912345678", encoding="utf-8")
    assert cli.main(["redact", str(f), "--off", "金额", "--hint"]) == 0
    out = capsys.readouterr().out
    assert "125 万元" in out and "<手机_1>" in out and out.startswith("（以下内容已脱敏")
    with pytest.raises(SystemExit):
        cli.main(["redact", str(f), "--off", "地址"])


st = pytest.importorskip("streamlit")
needs_new_streamlit = pytest.mark.skipif(tuple(int(x) for x in st.__version__.split(".")[:2]) < (1, 50),
                                         reason="页面用到 st.pills、横排容器等，需要 streamlit>=1.50")


@needs_new_streamlit
def test_app_example_redact_then_restore():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not at.exception
    at.button(key="ex_0").click().run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["本次换掉"] == "9 处" and metrics["会话映射"] == "9 条"
    assert "13912345678" not in at.code[0].value and "139 1234 5678" not in at.code[0].value
    at.button(key="ex_2").click().run()                   # 第二轮：同一客户沿用标签，只新增一个账号
    metrics = {m.label: (m.value, m.delta) for m in at.metric}
    assert metrics["会话映射"] == ("10 条", "+1 新增")
    at.button(key="btn_fill").click().run()
    at.button(key="btn_restore").click().run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["对不上的标签"] == "0 个" and "王建国" in at.code[-1].value


@needs_new_streamlit
def test_app_suspects_can_be_accepted():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    at.text_area(key="src").set_value("客户另一个号 12345678 也要查").run()
    at.button(key="btn_redact").click().run()
    assert at.warning and "12345678" in at.warning[0].value
    at.button(key="btn_sus").click().run()
    assert "12345678" not in at.code[0].value and not at.warning

from yindun.privacy import Redactor

CASES = {
    "手机": "联系电话 13912345678",
    "账号": "资金账号 8812345678901",
    "身份证": "证件 31010119900101123X",
    "邮箱": "邮箱 wjg@example.com",
    "内网地址": "服务器 192.168.10.21 和 10.1.2.3",
    "密钥": "api_key=sk-abc123456789",
    "金额": "浮亏 1,250,000 元",
}


def test_each_kind():
    for kind, s in CASES.items():
        r = Redactor().redact(s)
        assert kind in r.counts, (kind, r.text)
        assert Redactor.restore(r.text, r.mapping) == s


def test_names_and_orgs_consistent():
    s = "客户王建国先生问题；王建国先生再次来电；机构：华东某某投资公司。"
    r = Redactor().redact(s)
    assert r.text.count("<客户_1>") == 2 and "<机构_1>" in r.text
    assert Redactor.restore(r.text, r.mapping) == s


def test_custom_lists():
    r = Redactor(names=["李四"], orgs=["某某私募"]).redact("李四说某某私募要加仓")
    assert "李四" not in r.text and "某某私募" not in r.text


def test_no_false_positive_on_roles():
    r = Redactor().redact("营业部客户经理反馈，涉及客户信息的通知需符合公司信息安全要求，日期 2026-09-09，代码 RB2601")
    assert r.total == 0

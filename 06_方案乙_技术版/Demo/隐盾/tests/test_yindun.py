import io
import zipfile

import pytest

from yindun.files import decode, docx_text, read_bytes
from yindun.privacy import KINDS, Redactor, Session, label_counts, restore_detail, suspects
from yindun.samples import EXAMPLES

CASES = {
    "手机": "联系电话 13912345678",
    "账号": "资金账号 8812345678901",
    "身份证": "证件 31010119900101123X",
    "邮箱": "邮箱 wjg@example.com",
    "内网地址": "服务器 192.168.10.21 和 10.1.2.3",
    "密钥": "api_key=sk-abc123456789",
    "金额": "浮亏 1,250,000 元",
    "电话": "座机 021-12345678",
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
    r = Redactor(names=["李四"], orgs=["某某私募"], terms={"账号": ["7654321"]}).redact("李四说某某私募要加仓，号码 7654321")
    assert "李四" not in r.text and "某某私募" not in r.text and "7654321" not in r.text
    assert r.counts == {"客户": 1, "机构": 1, "账号": 1}


def test_no_false_positive_on_roles():
    r = Redactor().redact("营业部客户经理反馈，涉及客户信息的通知需符合公司信息安全要求，日期 2026-09-09，代码 RB2601")
    assert r.total == 0


# ---------- 与需知 v0.6 同源的补漏用例 ----------
@pytest.mark.parametrize("text, secrets, kept", [
    ("手机 139 1234 5678，备用 +86 139-8765-4321", ["139 1234 5678", "139-8765-4321"], ["手机", "备用"]),
    ("资金账号：88123456，客户号 1234567，卡号 6225 8801 1234 5678",
     ["88123456", "1234567", "6225 8801 1234 5678"], ["资金账号：", "客户号", "卡号"]),
    ("资金账号是 87654321，账号为7654321", ["87654321", "7654321"], ["资金账号是", "账号为"]),
    ("身份证 110101 19900307 1234，座机 021-12345678", ["110101 19900307 1234", "021-12345678"], ["身份证", "座机"]),
    ("客户是华泰证券，机构为中信期货，客户：招商银行", ["华泰证券", "中信期货", "招商银行"], ["客户是", "机构为"]),
    ("客户中信期货的持仓要单列", ["中信期货"], ["的持仓要单列"]),
    ("客户中信期货要求查一下持仓，客户华泰证券需要日报", ["中信期货", "华泰证券"], ["要求查一下持仓", "需要日报"]),
    ("客户张伟要求查一下，客户王芳说已经平仓了", ["张伟", "王芳"], ["要求查一下", "说已经平仓了"]),
    ("先做名单里这几位：张伟（13912345678）、王芳（13712345678）", ["张伟", "王芳", "13912345678", "13712345678"], ["这几位"]),
])
def test_spaced_numbers_orgs_and_names(text, secrets, kept):
    r = Redactor().redact(text)
    assert not [s for s in secrets if s in r.text], r.text
    assert all(k in r.text for k in kept), r.text
    assert Redactor.restore(r.text, r.mapping) == text


def test_business_words_untouched():
    plain = "保证金总额、周报总结、客户黄金持仓、高级经理、客户周报、2026-10-24、300 手、rb2510、客户为期货公司"
    assert Redactor().redact(plain).text == plain
    for phrase in ("客户想做期货，能不能给客户经理做个开户进度提醒", "客户希望投资黄金", "客户大多是银行", "客户还没开通期货",
                   "涉及客户信息的通知需符合公司信息安全要求", "Invalid token provided; the token expires soon",
                   "密码：请联系管理员重置", "螺纹 3650 元/吨，全市场成交 300 万手，市场规模 1.2 万亿",
                   "客户白天来电", "客户许久没来", "客户黄昏打来", "客户高度关注夜盘"):
        assert Redactor().redact(phrase).text == phrase, phrase
    colleagues = Redactor().redact("有疑问找王总 13812345678 或张老师 13912345678")
    assert colleagues.text == "有疑问找王总 <手机_1> 或张老师 <手机_2>"   # 同事称谓不当客户


# ---------- 独立版新增 ----------
@pytest.mark.parametrize("text, secret, kept", [
    ("mysql://root:Passw0rd@10.2.3.4:3306/crm", "root:Passw0rd", "mysql://"),
    ('{"password": "Abc@123456", "user": "admin"}', "Abc@123456", '"password": "'),
    ("token: ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8", "token: "),
    ("Authorization: Bearer abcdef1234567890", "abcdef1234567890", "Bearer "),
    ("key 是 sk-live9f8e7d6c5b4a3210ffee 别外传", "sk-live9f8e7d6c5b4a3210ffee", "别外传"),
    ("AK AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP", "AK "),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "MIIEow", ""),
])
def test_credentials(text, secret, kept):
    r = Redactor().redact(text)
    assert secret not in r.text and kept in r.text and "密钥" in r.counts, r.text
    assert Redactor.restore(r.text, r.mapping) == text


def test_money_with_units_and_context():
    r = Redactor().redact("账户权益 860 万，今天浮亏 125 万元，追保缺口 ¥300,000；螺纹 3650 元/吨，成交 300 万手")
    assert r.text == "账户权益 <金额_1>，今天浮亏 <金额_2>，追保缺口 <金额_3>；螺纹 3650 元/吨，成交 300 万手"


def test_labels_numbered_in_reading_order():
    r = Redactor().redact("权益 860 万，浮亏 125 万元，手机 13912345678 和 13712345678")
    assert r.text == "权益 <金额_1>，浮亏 <金额_2>，手机 <手机_1> 和 <手机_2>"
    assert r.mapping["<金额_1>"] == "860 万"


def test_kind_switches_and_keep_list():
    s = "客户王建国先生浮亏 125 万元，热线 95521，手机 13912345678"
    r = Redactor(kinds=[k for k in KINDS if k != "金额"], keep=["王建国"]).redact(s)
    assert r.text == "客户王建国先生浮亏 125 万元，热线 95521，手机 <手机_1>"


def test_session_keeps_labels_across_rounds_and_formats():
    s = Session()
    a = s.redact("客户王建国先生，手机 139 1234 5678，资金账号：88123456")
    b = s.redact("王建国先生又来电，手机还是 13912345678，另一个账户 88123457")
    assert "<客户_1>" in a.text and "<客户_1>" in b.text
    assert "<手机_1>" in b.text and "<账号_2>" in b.text and b.counts == {"账号": 1}
    assert label_counts(s.mapping) == {"客户": 1, "手机": 1, "账号": 2}
    assert s.restore("回电 <手机_1>").text == "回电 139 1234 5678"   # 不同写法合并，还原成第一次出现的写法


def test_continue_numbering_with_start_and_known():
    again = Redactor().redact("又是 13812345678，新号 13900001111", start={"手机": 4}, known={"13812345678": "<手机_2>"})
    assert again.text == "又是 <手机_2>，新号 <手机_5>" and again.counts == {"手机": 1}


def test_tolerant_restore():
    mapping = {"<客户_1>": "王建国", "<手机_1>": "13912345678", "<金额_12>": "125 万元"}
    answer = "〈客户_1〉、【客户_1】、\\<手机\\_1\\>、&lt;客户_1&gt;、<客户1>、客户_1、＜金额＿１２＞、<客户_1>、<地址_1>、<客户_9>"
    res = restore_detail(answer, mapping)
    assert res.text == "王建国、王建国、13912345678、王建国、王建国、王建国、125 万元、王建国、<地址_1>、<客户_9>"
    assert res.restored == 8 and res.fixed == 7
    assert res.unknown == ["<客户_9>", "<地址_1>"]


def test_restore_single_pass_and_brackets_kept():
    # 原文里恰好带标签样的文字，不会被二次替换；模型自己加的括号保留
    res = restore_detail("【<客户_1>】说：<客户_2>", {"<客户_1>": "<客户_2>", "<客户_2>": "李四"})
    assert res.text == "【<客户_2>】说：李四"


def test_suspects_only_hint_bare_numbers():
    r = Redactor().redact("两个号 12345678 和 88123456，日期 20261024，300 手，rb2510，资金账号：87654321")
    assert suspects(r.text) == ["12345678", "88123456"]


@pytest.mark.parametrize("name", list(EXAMPLES))
def test_examples_round_trip_and_answers_restore(name):
    s = Session()
    if name == "同一客户第二轮追问":
        s.redact(EXAMPLES["客户来电转研究所"]["text"])
    e = EXAMPLES[name]
    r = s.redact(e["text"])
    assert r.total > 0
    res = s.restore(e["answer"])
    assert res.restored > 0 and not res.unknown, res
    assert not [lb for lb in ("<客户_", "<手机_", "<账号_") if lb in res.text]


# ---------- 文件读取 ----------
def _docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r><w:r><w:tab/><w:t>尾</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_read_docx_and_gbk_text():
    assert docx_text(_docx(["客户王建国 &amp; 先生", "手机 13912345678"])) == "客户王建国 & 先生\t尾\n手机 13912345678\t尾"
    assert decode("客户王建国".encode("gb18030")) == "客户王建国"
    assert read_bytes("a.txt", "﻿手机".encode("utf-8")) == "手机"
    with pytest.raises(ValueError):
        read_bytes("a.xlsx", b"PK")

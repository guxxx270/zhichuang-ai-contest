"""安全合规：材料进模型前脱敏、仓库地址白名单、凭据文件不读、模型返回容错。"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from xuzhi.audit import count_plain_sensitive
from xuzhi.drafts import (
    CodeFile,
    RepoBundle,
    check_clone_url,
    draft_context_for_llm,
    ingest_repo_dir,
    is_secret_path,
    parse_draft,
    redact_material,
)
from xuzhi.pipeline import analyze
from xuzhi.pipeline.intake import build_questions, extract_card, refine_with_llm
from xuzhi.pipeline.prototype import adapt_draft_with_llm
from xuzhi.privacy import Redactor, label_counts


@pytest.mark.parametrize("text, secrets, kept", [
    ("手机 139 1234 5678，备用 +86 139-8765-4321", ["139 1234 5678", "139-8765-4321"], ["手机", "备用"]),
    ("资金账号：88123456，客户号 1234567，卡号 6225 8801 1234 5678",
     ["88123456", "1234567", "6225 8801 1234 5678"], ["资金账号：", "客户号", "卡号"]),
    ("资金账号是 87654321，账号为7654321", ["87654321", "7654321"], ["资金账号是", "账号为"]),
    ("身份证 110101 19900307 1234，座机 021-12345678", ["110101 19900307 1234", "021-12345678"], ["身份证", "座机"]),
    ("客户是华泰证券，机构为中信期货，客户：招商银行", ["华泰证券", "中信期货", "招商银行"], ["客户是", "机构为"]),
    ("客户中信期货的持仓要单列", ["中信期货"], ["的持仓要单列"]),
    ("客户中信期货要求查一下持仓，客户华泰证券需要日报", ["中信期货", "华泰证券"], ["要求查一下持仓", "需要日报"]),   # 名字后接要求 / 需要照样认
    ("客户张伟要求查一下，客户王芳说已经平仓了", ["张伟", "王芳"], ["要求查一下", "说已经平仓了"]),
    ("先做名单里这几位：张伟（13912345678）、王芳（13712345678）", ["张伟", "王芳", "13912345678", "13712345678"], ["这几位"]),
])
def test_redactor_catches_spaced_numbers_orgs_and_names(text, secrets, kept):
    r = Redactor().redact(text)
    assert not [s for s in secrets if s in r.text], r.text
    assert all(k in r.text for k in kept), r.text
    assert Redactor.restore(r.text, r.mapping) == text


def test_redactor_categories_and_untouched_business_words():
    r = Redactor().redact("座机 021-12345678，资金账号 88123456")
    assert r.text == "座机 <电话_1>，资金账号 <账号_1>"      # 短账号只换数字，留下上下文词
    plain = "保证金总额、周报总结、客户黄金持仓、高级经理、客户周报、2026-10-24、300 手、rb2510、客户为期货公司"
    assert Redactor().redact(plain).text == plain
    # 「客户想做期货」「客户大多是银行」是动作和泛称：换成机构标签会把期货语境一起抹掉
    for phrase in ("客户想做期货，能不能给客户经理做个开户进度提醒", "客户希望投资黄金", "客户大多是银行", "客户还没开通期货",
                   "涉及客户信息的通知需符合公司信息安全要求"):
        assert Redactor().redact(phrase).text == phrase
    colleagues = Redactor().redact("有疑问找王总 13812345678 或张老师 13912345678")
    assert colleagues.text == "有疑问找王总 <手机_1> 或张老师 <手机_2>"   # 同事称谓不当客户
    # 新标签可接着已有编号往后编：Markdown 转义的原话标签也算
    assert label_counts("原话 \\<手机\\_1\\> 和 <账号_2>") == {"手机": 1, "账号": 2}
    again = Redactor().redact("又是 13812345678，新号 13900001111", start={"手机": 4}, known={"13812345678": "<手机_2>"})
    assert again.text == "又是 <手机_2>，新号 <手机_5>" and again.counts == {"手机": 1}


class _EchoDraftLLM:
    """回显模型：把底稿 HTML 原样还回去，再附上收到的原话和仓库代码摘录。"""
    mode = "api"

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.current_purpose = ""

    def chat(self, system, user, temperature=0.1, json_mode=False, purpose=""):
        self.sent.append(user)
        p = json.loads(user)
        code = p["现有系统材料"]["repos"][0]["files"][0]["excerpt"]
        return p["底稿完整HTML"].replace("</body>", f'<div id="demand">{p["本次原话"]}</div><pre id="repo">{code}</pre></body>')

    def chat_json(self, system, user):
        return {}


def test_material_labels_continue_after_demand_labels_and_restore_in_place():
    """原话手机 A、底稿手机 B、仓库手机 C：各自的标签不撞号，回显后各回各位。"""
    a_phone, b_phone, c_phone = "13800000001", "13900000002", "13700000003"
    demand = Redactor().redact(f"通讯录页面加一列备注，有问题打 {a_phone}").text
    draft = parse_draft("contacts.html", (
        "<!doctype html><html><head><title>客户联系人</title><style>.grid{border:1px solid #ccc}</style></head>"
        "<body><h1>客户联系人</h1><h2>名单</h2><table class='grid'><tr><th>联系人</th><th>电话</th></tr>"
        f"<tr><td>值班</td><td>{b_phone}</td></tr></table><button>查</button><button>导出</button></body></html>"))
    repo = RepoBundle(url="https://github.com/acme/demo.git", tree=["app/contacts.py"],
                      files=[CodeFile(path="app/contacts.py", kind="backend", text=f"DUTY_PHONE = '{c_phone}'\n")])
    llm = _EchoDraftLLM()
    page, used = adapt_draft_with_llm(extract_card(demand), [draft], llm, repos=[repo], demand_text=demand)
    assert page and used is not None
    assert not [p for p in (a_phone, b_phone, c_phone) if p in llm.sent[0]]
    said = re.search(r'<div id="demand">(.*?)</div>', page).group(1)
    code = re.search(r'<pre id="repo">(.*?)</pre>', page, flags=re.S).group(1)
    table = page[page.index("<table"): page.index("</table>")]
    assert "<手机_1>" in said and b_phone not in said and c_phone not in said   # 原话标签保持原样
    assert c_phone in code and b_phone not in code
    assert b_phone in table and c_phone not in table


def test_material_metadata_redacted_in_every_outbound_prompt():
    """底稿文件名、title、表头和仓库路径里的手机号：问清润色、写单润色、出样改稿的出站内容里都不能有明文。"""
    phone = "13912345678"
    draft = parse_draft(f"客户{phone}名单.html", (
        f"<html><head><title>联系人{phone}</title></head><body><h1>名单</h1><h2>联系方式</h2>"
        f"<table><tr><th>姓名</th><th>手机{phone}</th></tr><tr><td>甲</td><td>1</td></tr></table>"
        "<button>查</button><button>导出</button></body></html>"))
    repo = RepoBundle(url="https://github.com/acme/demo.git", tree=[f"reports/{phone}/", f"reports/{phone}/nav.py"],
                      files=[CodeFile(path=f"reports/{phone}/nav.py", kind="backend", text="def nav():\n    return 1\n")])

    class _SpyLLM:
        mode = "api"
        model = last_model = "spy"
        note = ""

        def __init__(self) -> None:
            self.current_purpose = ""
            self.sent: list[tuple[str, str]] = []

        def chat_json(self, system, user):
            self.sent.append((self.current_purpose, user))
            return {}

        def chat(self, system, user, temperature=0.1, json_mode=False, purpose=""):
            self.sent.append((purpose or self.current_purpose, user))
            return ""

    llm = _SpyLLM()
    analyze("净值日报能不能加一列对标指数，放在单位净值旁边。", llm=llm, drafts=[draft], repos=[repo])
    by_purpose: dict[str, list[str]] = {}
    for purpose, user in llm.sent:
        by_purpose.setdefault(purpose, []).append(user)
    assert {"问清润色", "写单润色", "出样改稿"} <= set(by_purpose)
    for purpose in ("问清润色", "写单润色", "出样改稿"):
        for user in by_purpose[purpose]:
            assert phone not in user and count_plain_sensitive(user) == 0, purpose


def test_clone_url_whitelist(monkeypatch):
    import socket

    def resolve(host, port):
        ip = "10.1.2.3" if host == "private.example.com" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    assert check_clone_url("https://github.com/acme/demo.git") == ""
    assert check_clone_url("https://gitee.com/acme/demo.git") == ""
    for bad in (
        "file:///Users/x/repo.git",
        "ssh://git@github.com/acme/demo.git",
        "git@github.com:acme/demo.git",
        "http://127.0.0.1/repo.git",
        "http://localhost:8080/repo.git",
        "http://10.1.2.3/repo.git",
        "http://private.example.com/repo.git",
        "http://192.168.1.10/repo.git",
        "http://169.254.169.254/latest/meta-data.git",
        "https://user:pw@github.com/acme/demo.git",
    ):
        assert check_clone_url(bad), bad


def test_clone_url_under_proxy_fake_ip(monkeypatch):
    """本机代理 fake-ip 模式：域名都解析成 198.18.x.x。公网代码托管放行，其余域名照拒，直接写 IP 的也照拒。"""
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.31", 0))])
    assert check_clone_url("https://github.com/acme/demo.git") == ""
    assert check_clone_url("https://gitee.com/acme/demo.git") == ""
    assert check_clone_url("https://api.github.com/repos/acme/demo") == ""
    assert "fake-ip" in check_clone_url("https://git.corp.example.com/acme/demo.git")
    assert "fake-ip" in check_clone_url("https://evilgithub.com/acme/demo.git")
    assert "内网" in check_clone_url("http://198.18.0.31/repo.git")


@pytest.mark.parametrize("host, username, auth_user", [
    ("github.com", "wrong-user", "x-access-token"),
    ("gitee.com", "alice", "alice"),
])
def test_clone_keeps_token_out_of_command_with_branch(monkeypatch, host, username, auth_user):
    """合并分支选择后，真实 clone 入口仍只通过环境变量传递 Token。"""
    import base64
    from xuzhi import drafts as D

    token = "test-clone-token"
    clone_url = f"https://{host}/acme/demo.git"
    monkeypatch.setattr(D, "_is_private_host", lambda host: False)
    monkeypatch.delenv("XUZHI_GIT_SSL_NO_VERIFY", raising=False)
    monkeypatch.delenv("GIT_SSL_NO_VERIFY", raising=False)
    calls = []

    def clone(args, **kwargs):
        calls.append(args)
        assert token not in " ".join(args)
        assert args[-2] == clone_url
        assert args[args.index("--branch") + 1] == "release"
        assert "protocol.allow=never" in args
        assert "protocol.http.allow=always" in args
        assert "protocol.https.allow=always" in args
        env = kwargs["env"]
        encoded = base64.b64encode(f"{auth_user}:{token}".encode()).decode()
        assert env["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {encoded}"
        dest = Path(args[-1])
        dest.mkdir()
        (dest / "app.py").write_text("print(1)\n", encoding="utf-8")

    monkeypatch.setattr(D.subprocess, "run", clone)
    _, repos = D.load_from_git(clone_url, token=token, username=username, branch="release")
    assert len(calls) == 1
    assert repos[0].ref == "release"
    assert token not in repos[0].url + repos[0].note


@pytest.mark.parametrize("url", [
    "file:///tmp/repo.git",
    "ssh://git@github.com/acme/demo.git",
    "git@github.com:acme/demo.git",
    "http://127.0.0.1/repo.git",
    "http://10.1.2.3/group/repo",
    "https://user:password@github.com/acme/demo.git",
])
def test_rejected_clone_cannot_reach_archive_fallback(monkeypatch, url):
    from xuzhi import drafts as D

    def unexpected(*args, **kwargs):
        pytest.fail("被拒绝的仓库地址不应触发 Git 或归档网络请求")

    monkeypatch.setattr(D.subprocess, "run", unexpected)
    monkeypatch.setattr(D, "_http_get_bytes", unexpected)
    with pytest.raises(RuntimeError):
        D.load_from_git(url, token="test-clone-token")


def test_secret_paths_skipped(tmp_path: Path):
    for name in (".env", ".env.local", "config/secrets.yml", "certs/server.pem", "deploy/id_rsa", "app/credentials.json"):
        assert is_secret_path(name), name
    for name in ("src/app.py", "web/index.html", "config/application.yml", "README.md"):
        assert not is_secret_path(name), name
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "src" / "secrets.yml").write_text("password: abc123456", encoding="utf-8")
    (tmp_path / ".env").write_text("LLM_API_KEY=sk-xxxx", encoding="utf-8")
    _, bundle = ingest_repo_dir(tmp_path)
    paths = {f.path for f in bundle.files}
    assert "src/app.py" in paths and "src/secrets.yml" not in paths and ".env" not in paths


def test_material_redacted_before_model():
    html = "<html><body><table><tr><th>客户</th><th>手机</th></tr><tr><td>客户张伟先生</td><td>13912345678</td></tr></table></body></html>"
    d = parse_draft("客户表.html", html)
    repo = RepoBundle(url="https://github.com/acme/demo.git", tree=["app.py"],
                      files=[CodeFile(path="app.py", kind="backend", text="db = connect(host='10.20.30.40', password='abc123456')")])
    mapping: dict[str, str] = {}
    ctx = draft_context_for_llm([d], [repo], mapping=mapping)
    blob = str(ctx)
    assert "13912345678" not in blob and "10.20.30.40" not in blob and "abc123456" not in blob
    assert mapping and Redactor.restore(ctx["html_drafts"][0]["html"], mapping).count("13912345678") == 1
    assert "13912345678" not in redact_material("电话 13912345678")


def test_model_null_extra_questions_do_not_crash():
    class _LLM:
        mode = "api"
        model = "x"

        def chat_json(self, system, user):
            return {"title": "T", "extra_questions": None, "features": ["a", "b"]}

    text = "净值日报能不能加一列对标指数"
    card = extract_card(text, "")
    qs = build_questions(text, card)
    card2, qs2 = refine_with_llm(text, card, qs, _LLM())
    assert card2.title and len(qs2) == len(qs)

    class _LLM2(_LLM):
        def chat_json(self, system, user):
            return {"extra_questions": [{"question": "要不要按产品分组？", "impact": "High"}, {"question": ""}, "junk"]}

    _, qs3 = refine_with_llm(text, card, qs, _LLM2())
    extra = [q for q in qs3 if q.tag == "模型"]
    assert len(extra) == 1 and extra[0].impact == "高"

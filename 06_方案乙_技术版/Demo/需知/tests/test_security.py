"""安全合规：材料进模型前脱敏、仓库地址白名单、凭据文件不读、模型返回容错。"""
from __future__ import annotations

from pathlib import Path

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
from xuzhi.pipeline.intake import build_questions, extract_card, refine_with_llm
from xuzhi.privacy import Redactor


def test_clone_url_whitelist():
    assert check_clone_url("https://github.com/acme/demo.git") == ""
    assert check_clone_url("https://gitee.com/acme/demo.git") == ""
    for bad in (
        "file:///Users/x/repo.git",
        "ssh://git@github.com/acme/demo.git",
        "git@github.com:acme/demo.git",
        "http://127.0.0.1/repo.git",
        "http://localhost:8080/repo.git",
        "http://10.1.2.3/repo.git",
        "http://192.168.1.10/repo.git",
        "http://169.254.169.254/latest/meta-data.git",
        "https://user:pw@github.com/acme/demo.git",
    ):
        assert check_clone_url(bad), bad


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

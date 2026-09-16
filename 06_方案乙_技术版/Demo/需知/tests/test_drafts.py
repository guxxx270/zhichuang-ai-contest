from pathlib import Path
import re

from xuzhi.drafts import (
    draft_context_for_llm,
    draft_from_bytes,
    ingest_repo_dir,
    pick_draft,
    summarize,
)
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze
from xuzhi.pipeline.architect import build_architecture
from xuzhi.pipeline.intake import extract_card
from xuzhi.pipeline.prototype import _extract_html, apply_prototype_view, render, render_source


SAMPLE_DRAFT = Path(__file__).resolve().parents[1] / "data" / "drafts" / "净值日报_示例底稿.html"


def test_draft_parse_and_pick():
    d = draft_from_bytes(SAMPLE_DRAFT.name, SAMPLE_DRAFT.read_bytes(), source="sample")
    assert d.title == "净值日报（示例底稿）" or "净值日报" in d.title
    assert "单位净值" in d.table_headers
    assert pick_draft([d], {"净值日报", "对标指数"}).name == d.name
    assert "单位净值" in summarize([d])


def test_draft_context_includes_html():
    d = draft_from_bytes(SAMPLE_DRAFT.name, SAMPLE_DRAFT.read_bytes(), source="sample")
    ctx = draft_context_for_llm([d], html_limit=5000)
    assert ctx["draft_count"] == 1
    assert "<table" in ctx["drafts"][0]["html"].lower()
    assert "单位净值" in ctx["drafts"][0]["html"]


def test_extract_html_from_model_output():
    raw = "好的，如下：\n```html\n<!doctype html><html><body><table><tr><th>产品</th></tr></table></body></html>\n```\n"
    out = _extract_html(raw)
    assert out.lower().startswith("<!doctype")
    assert "<table" in out.lower()


def test_ingest_repo_reads_frontend_and_backend(tmp_path: Path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend" / "NavReport.vue").write_text(
        "<template><div>净值日报<table><tr><th>单位净值</th></tr></table></div></template>",
        encoding="utf-8",
    )
    (tmp_path / "backend" / "api.py").write_text(
        "def get_nav():\n    return {'unit_nav': 1.0, 'benchmark': '沪深300'}\n",
        encoding="utf-8",
    )
    (tmp_path / "page.html").write_text(
        SAMPLE_DRAFT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    drafts, bundle = ingest_repo_dir(tmp_path, url="https://example.com/demo.git")
    assert drafts and any("page.html" in d.name or d.name.endswith(".html") for d in drafts)
    kinds = {f.kind for f in bundle.files}
    assert "frontend" in kinds or "html" in kinds
    assert "backend" in kinds
    ctx = draft_context_for_llm(drafts, [bundle], keywords={"净值", "对标"}, code_limit=8000)
    assert ctx["repo_count"] == 1
    blob = str(ctx["repos"])
    assert "api.py" in blob or "NavReport.vue" in blob
    arch = build_architecture(extract_card("净值日报加一列对标指数"), "净值日报加一列对标指数", drafts=drafts, repos=[bundle])
    assert "代码仓库" in [s[0] for s in arch.stack]
    assert any(d.id == "D0" for d in arch.decisions)


def test_prototype_from_draft_adds_column():
    draft = draft_from_bytes(SAMPLE_DRAFT.name, SAMPLE_DRAFT.read_bytes(), source="sample")
    a = analyze(
        "净值日报能不能加一列对标指数，就放在单位净值旁边。",
        llm=LLM(mode="mock"),
        drafts=[draft],
        llm_polish=False,
    )
    html = a.prototype_html
    assert a.prototype_source_html
    assert "需知 · 出样" in html
    assert "原页复刻" in html or "基于底稿" in html or draft.name in html
    assert "对标指数" in html
    assert "单位净值" in html
    assert "稳健一号" in html
    assert "#f6f4ef" in html or "f6f4ef" in html
    assert any(d.id == "D0" for d in a.architecture.decisions)
    assert a.architecture.draft_names
    assert "前端底稿" in [s[0] for s in a.architecture.stack]
    mobile = apply_prototype_view(a.prototype_source_html, mobile=True, client_view=False)
    assert "单位净值" in mobile and "phone" in mobile


def test_without_draft_still_uses_template():
    a = analyze("净值日报能不能加一列较上一交易日变动", llm_polish=False)
    assert "较上一交易日变动" in a.prototype_html
    assert not a.architecture.draft_names


def test_architecture_mentions_drafts():
    draft = draft_from_bytes(SAMPLE_DRAFT.name, SAMPLE_DRAFT.read_bytes())
    card = extract_card("净值日报加一列对标指数")
    arch = build_architecture(card, "净值日报加一列对标指数", drafts=[draft])
    assert "底稿" in arch.coverage_line or "材料" in arch.coverage_line
    assert "D0" in arch.adr_md
    html = render(card, drafts=[draft])
    assert "对标指数" in html
    assert render_source(card, drafts=[draft], llm=LLM(mode="mock"))


def test_add_column_asks_display_format():
    from xuzhi.pipeline.intake import build_questions, extract_card

    text = "报表加一列sortino"
    card = extract_card(text)
    qs = build_questions(text, card, table_headers=["产品", "单位净值", "回撤"])
    ids = {q.id for q in qs}
    assert {"C_FMT", "C_NULL", "C_POS"} <= ids
    fmt = next(q for q in qs if q.id == "C_FMT")
    assert "sortino" in fmt.question
    assert "百分数" in fmt.question or "小数" in fmt.question
    pos = next(q for q in qs if q.id == "C_POS")
    assert "单位净值" in pos.question or "回撤" in pos.question


def test_non_add_column_skips_format_probes():
    from xuzhi.pipeline.intake import build_questions, extract_card

    text = "登录页加一个忘记密码入口，点了发邮件重置。"
    card = extract_card(text)
    qs = build_questions(text, card, has_materials=True)
    assert all(not q.id.startswith("C_") for q in qs)
    assert all(q.tag != "期货" for q in qs)


def test_futures_text_still_gets_futures_probes():
    from xuzhi.pipeline.intake import build_questions, extract_card

    text = "净值日报能不能加一列对标指数，结算后每天出。"
    card = extract_card(text)
    qs = build_questions(text, card, has_materials=True)
    assert any(q.tag == "期货" for q in qs)


def test_style_check_rejects_redesign():
    from xuzhi.pipeline.prototype import _html_ok, _preserves_draft_style

    draft = draft_from_bytes(SAMPLE_DRAFT.name, SAMPLE_DRAFT.read_bytes(), source="sample")
    card = extract_card("净值日报加一列对标指数")
    alien = (
        "<!doctype html><html><head><style>body{background:#111;color:#0f0}</style></head>"
        "<body><div class='neon-dash'><h1>净值日报加一列对标指数</h1></div></body></html>"
    )
    assert not _preserves_draft_style(alien, draft)
    assert not _html_ok(alien, card, draft)
    kept = render(card, drafts=[draft], llm=None)
    assert "background: #f6f4ef" in kept or "#f6f4ef" in kept
    assert "class=\"top\"" in kept or 'class="top"' in kept


def test_confirm_message_not_futures_for_generic():
    from xuzhi.pipeline.intake import build_questions, confirm_message, extract_card

    text = "登录页加一个忘记密码入口，点了发邮件重置。"
    card = extract_card(text)
    qs = build_questions(text, card, has_materials=True)
    msg = confirm_message(card, qs, text)
    assert "结算" not in msg
    assert "夜盘" not in msg
    assert "主力" not in msg
    assert "落实" in card.goal or "登录" in card.goal or "密码" in card.goal


def test_related_page_focus_and_inert_nav():
    from xuzhi.drafts import related_drafts
    from xuzhi.pipeline.prototype import render_from_drafts

    login = draft_from_bytes(
        "login.html",
        "<!doctype html><html><body><h1>登录</h1><a href='report.html'>净值日报</a></body></html>".encode("utf-8"),
        source="upload",
    )
    report = draft_from_bytes(
        "report.html",
        (
            "<!doctype html><html><body>"
            "<nav><a href='login.html'>登录</a><a href='settings.html'>设置</a></nav>"
            "<h1>净值日报</h1><table><tr><th>单位净值</th></tr><tr><td>1.1</td></tr></table>"
            "</body></html>"
        ).encode("utf-8"),
        source="upload",
    )
    settings = draft_from_bytes(
        "settings.html",
        "<!doctype html><html><body><h1>系统设置</h1></body></html>".encode("utf-8"),
        source="upload",
    )
    card = extract_card("净值日报加一列对标指数")
    keys = {"净值日报", "对标指数", "单位净值"}
    related = related_drafts([login, report, settings], keys)
    assert related and related[0].name == "report.html"
    html = render_from_drafts(card, [login, report, settings])
    assert html and "单位净值" in html
    assert "聚焦本页" in html
    assert 'data-xz-inert="1"' in html or "data-xz-inert=1" in html


def test_git_shell_html_not_forced_as_page(tmp_path: Path):
    """仓库里任意不可见界面的 HTML 空壳都不能当成业务底稿硬复刻。"""
    from xuzhi.drafts import ingest_repo_dir
    from xuzhi.pipeline.intake import extract_card
    from xuzhi.pipeline.prototype import render, render_from_drafts

    (tmp_path / "public").mkdir()
    # 文件名故意不用 index.html，证明按「是否可见界面」判断，不按文件名
    (tmp_path / "public" / "entry.html").write_text(
        "<!doctype html><html><head><title>App</title></head>"
        "<body><div id='app'></div><script src='/js/app.js'></script>"
        "<script src='/js/chunk.js'></script></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.py").write_text("def ping(): return 1\n", encoding="utf-8")
    drafts, bundle = ingest_repo_dir(tmp_path, url="https://example.com/spa.git")
    assert not drafts
    assert any(f.path.endswith("entry.html") for f in bundle.files)
    card = extract_card("持仓分布增加时序走势 Tab")
    assert render_from_drafts(card, drafts) is None
    html = render(card, drafts=drafts, repos=[bundle], demand_text="持仓分布增加时序走势 Tab")
    assert "时序走势" in html or "持仓" in html
    assert "id='app'" not in html and 'id="app"' not in html


def test_upload_draft_adds_column_into_original_table():
    """加一列必须写进原始 <table>，不能只在外面加区块。"""
    from xuzhi.pipeline.intake import extract_card
    from xuzhi.pipeline.prototype import render_source

    draft = draft_from_bytes(
        "list.html",
        (
            "<!doctype html><html><body><h1>名单</h1>"
            "<table><tr><th>姓名</th><th>部门</th></tr>"
            "<tr><td>张三</td><td>研发</td></tr></table></body></html>"
        ).encode("utf-8"),
        source="upload",
    )
    text = "在已有表里新增一列金额"
    card = extract_card(text)
    html = render_source(card, drafts=[draft], demand_text=text)
    assert "<th>金额</th>" in html
    # 表头行应同时含原列与新列
    assert re.search(r"<tr>\s*<th>姓名</th>\s*<th>部门</th>\s*<th>金额</th>", html, re.I | re.S)
    assert "xz-demand-section" not in html or html.index("<th>金额</th>") < html.find("xz-demand-section")


def test_upload_draft_adds_column_when_header_is_td():
    from xuzhi.pipeline.intake import extract_card
    from xuzhi.pipeline.prototype import render_source

    draft = draft_from_bytes(
        "list2.html",
        (
            "<!doctype html><html><body>"
            "<table><tr><td>姓名</td><td>部门</td></tr>"
            "<tr><td>李四</td><td>产品</td></tr></table></body></html>"
        ).encode("utf-8"),
        source="upload",
    )
    text = "表中增加一列备注"
    html = render_source(extract_card(text), drafts=[draft], demand_text=text)
    assert re.search(r"<tr>\s*<td>姓名</td>\s*<td>部门</td>\s*<td><b>备注</b></td>", html, re.I | re.S)


def test_add_one_column_does_not_invent_extra_columns():
    """「加一列X」只加 X，不把标题/页面名拆成第二列。"""
    from xuzhi.pipeline.intake import extract_card
    from xuzhi.pipeline.prototype import render_source

    draft = draft_from_bytes(
        "staff.html",
        (
            "<!doctype html><html><body>"
            "<table><tr><th>姓名</th><th>部门</th></tr>"
            "<tr><td>张三</td><td>研发</td></tr></table></body></html>"
        ).encode("utf-8"),
        source="upload",
    )
    text = "员工花名册加一列职级"
    html = render_source(extract_card(text), drafts=[draft], demand_text=text)
    assert html.count("<th>职级</th>") == 1
    assert "<th>员工</th>" not in html
    assert "<th>花名册</th>" not in html
    assert html.count("<th>") == 3


def test_upload_draft_deletes_and_renames_columns_in_table():
    from xuzhi.pipeline.intake import extract_card
    from xuzhi.pipeline.prototype import render_source
    from xuzhi.textutil import find_removed_columns, find_renamed_columns

    assert find_removed_columns("删除部门列") == ["部门"]
    assert find_renamed_columns("把姓名列改成客户名") == [("姓名", "客户名")]

    draft = draft_from_bytes(
        "list3.html",
        (
            "<!doctype html><html><body>"
            "<table><tr><th>姓名</th><th>部门</th><th>金额</th></tr>"
            "<tr><td>张三</td><td>研发</td><td>12</td></tr></table></body></html>"
        ).encode("utf-8"),
        source="upload",
    )
    text = "把姓名列改成客户名，并删除部门列"
    html = render_source(extract_card(text), drafts=[draft], demand_text=text)
    assert "<th>客户名</th>" in html
    assert "<th>金额</th>" in html
    assert "<th>部门</th>" not in html
    assert "<th>姓名</th>" not in html
    assert "xz-demand-section" not in html


def test_materials_report_lists_repo_files(tmp_path: Path):
    from xuzhi.drafts import CodeFile, RepoBundle, materials_report

    repo = RepoBundle(
        url="https://example.com/demo.git",
        tree=["src/", "src/api.py"],
        files=[CodeFile(path="src/api.py", kind="backend", text="x=1")],
        note="1 个源文件 / 0 个可复刻 HTML",
    )
    lines = materials_report([], [repo])
    assert any("已拉取仓库" in x for x in lines)
    assert any("src/api.py" in x for x in lines)


def test_with_draft_ignores_llm_redesign():
    """有底稿时即使给了 api 模式假 LLM，也应走底稿复刻，不落到需知模板。"""
    class FakeApi:
        mode = "api"

        def chat(self, *a, **k):
            return "<!doctype html><html><body><div class='xz-top'><h2>需知风重画</h2></div></body></html>"

        def chat_json(self, *a, **k):
            return {}

    draft = draft_from_bytes(SAMPLE_DRAFT.name, SAMPLE_DRAFT.read_bytes(), source="sample")
    card = extract_card("净值日报加一列对标指数")
    html = render_source(card, drafts=[draft], llm=FakeApi())
    assert "稳健一号" in html
    assert "xz-top" not in html or "原页复刻" in html
    assert "#f6f4ef" in html or "单位净值" in html


def test_spa_shell_is_not_visual():
    from xuzhi.drafts import is_visual_html

    spa = """<!doctype html><html><head><title>九瑞投研平台</title>
    <script src="/static/js/chunk-vendors.js"></script>
    <script src="/static/js/app.js"></script></head>
    <body><div id="app"></div></body></html>"""
    assert not is_visual_html(spa)
    assert is_visual_html(SAMPLE_DRAFT.read_text(encoding="utf-8"))


def test_spa_shell_generates_named_tabs_from_demand():
    spa = draft_from_bytes(
        "investpre.html",
        (
            "<!doctype html><html><head><title>九瑞投研平台</title></head>"
            "<body><div id='app'></div>"
            "<script src='/js/app.js'></script><script src='/js/chunk.js'></script>"
            "</body></html>"
        ).encode("utf-8"),
        source="url",
    )
    text = (
        "在【环境变量】【因子分析】模块下的持仓分布模块下，增加不同品种的因子时序图。"
        "原来的柱状图改名为：截面分布tab页，新的时序图改名为：时序走势。"
        "时序图默认展示 Factor_MemberRanking 因子。"
        "时序图默认筛选持仓量（weight）最大品种。"
        "品种筛选：可多选，可全选，可清空选项，可模糊搜索。"
    )
    a = analyze(text, llm=LLM(mode="mock"), drafts=[spa], llm_polish=False)
    html = a.prototype_html
    assert "时序走势" in html
    assert "截面分布" in html
    assert "Factor_MemberRanking" in html
    assert "<svg" in html.lower()
    assert "全选" in html and "清空" in html


def test_generic_demand_has_no_cross_section_tabs():
    """别的需求不应默认冒出「截面分布/时序走势」。"""
    a = analyze("登录页加一个忘记密码入口，点了发邮件重置。", llm_polish=False)
    html = a.prototype_html
    assert "截面分布" not in html
    assert "时序走势" not in html
    assert "Factor_MemberRanking" not in html
    assert "螺纹钢" not in html


def test_generic_ui_demand_uses_named_structure_only():
    """非投研需求也应只按原话出 Tab/标识，不套期货样例。"""
    text = (
        "在【客户管理】模块下，把列表页改名为：客户总览tab页，新增跟进记录tab页。"
        "支持按区域筛选，可多选，可全选，可模糊搜索。"
        "默认展示 Status_Active。"
    )
    a = analyze(text, llm_polish=False)
    html = a.prototype_html
    assert "客户总览" in html
    assert "跟进记录" in html
    assert "Status_Active" in html
    assert "全选" in html and "清空" in html
    assert "截面分布" not in html
    assert "时序走势" not in html
    assert "螺纹钢" not in html
    assert "Factor_MemberRanking" not in html


def test_normalize_url_adds_https():
    from xuzhi.drafts import normalize_url

    assert normalize_url("www.example.com") == "https://www.example.com"
    assert normalize_url("example.com/path#/a") == "https://example.com/path#/a"
    assert normalize_url("10.66.200.208/root/app.git") == "https://10.66.200.208/root/app.git"
    assert normalize_url("https://www.example.com") == "https://www.example.com"
    assert normalize_url("http://intranet/page") == "http://intranet/page"
    assert normalize_url("git@github.com:org/repo.git").startswith("git@")
    assert normalize_url("ssh://git@host/repo.git").startswith("ssh://")


def test_inject_git_auth_and_redact():
    from xuzhi.drafts import inject_git_auth, redact_secrets

    gh = inject_git_auth("https://github.com/org/repo.git", token="ghp_secret123")
    assert "x-access-token:ghp_secret123@" in gh
    assert inject_git_auth("https://github.com/org/repo.git", token="") == "https://github.com/org/repo.git"

    gl = inject_git_auth("http://10.66.200.208/root/app.git", token="glpat-abc")
    assert gl.startswith("http://oauth2:glpat-abc@10.66.200.208/")

    gitee = inject_git_auth("https://gitee.com/org/repo.git", token="tok", username="alice")
    assert "alice:tok@" in gitee

    msg = redact_secrets(
        "fail https://oauth2:glpat-abc@10.66.200.208/root/app.git glpat-abc",
        "glpat-abc",
    )
    assert "glpat-abc" not in msg
    assert "***" in msg


def test_load_urls_entries_per_link_errors(monkeypatch):
    from xuzhi import drafts as D

    def fake_load(url, token="", username="", git_only=False):
        if "fail" in url:
            raise RuntimeError(f"auth failed tok={token}")
        if "empty" in url:
            return [D.parse_draft("e.html", "<html><body><div id='app'></div></body></html>", source="url", note="spa-shell")], []
        return [D.parse_draft("ok.html", "<html><body><h1>A</h1><h2>B</h2><table><tr><th>x</th><td>1</td></tr></table><button>a</button><button>b</button></body></html>", source="url")], []

    monkeypatch.setattr(D, "load_from_url", fake_load)
    _d, _r, errs = D.load_urls(
        entries=[
            {"url": "https://ok.example/a", "token": ""},
            {"url": "https://fail.example/b.git", "token": "secretTOK"},
            {"url": "https://empty.example/c", "token": "t2"},
        ]
    )
    assert any(e.startswith("未读到：") and "fail.example" in e for e in errs)
    assert all("secretTOK" not in e for e in errs)
    assert any(e.startswith("无有效内容：") and "empty.example" in e for e in errs)
    assert not any("ok.example" in e and e.startswith("未读到：") for e in errs)


def test_load_urls_git_only_rejects_web_page():
    from xuzhi.drafts import load_urls

    _d, _r, errs = load_urls(entries=[{"url": "https://www.example.com/app"}], git_only=True)
    assert errs and errs[0].startswith("未读到：")
    assert "Git" in errs[0] or "上传 HTML" in errs[0]


def test_draft_usability_hints_spa_and_files_folder():
    from xuzhi.drafts import draft_usability_hints, parse_draft

    spa = parse_draft(
        "investpre.html",
        "<!doctype html><html><body><div id='app'></div>"
        "<script src='/js/app.js'></script><script src='/js/chunk.js'></script></body></html>",
        source="upload",
    )
    hints = draft_usability_hints([spa])
    assert hints and "空" in hints[0]

    broken = parse_draft(
        "page.html",
        "<!doctype html><html><head><link rel='stylesheet' href='page_files/style.css'></head>"
        "<body><h1>标题</h1><h2>副标题</h2><table><tr><th>A</th><td>1</td></tr></table>"
        "<button>查</button><button>导出</button></body></html>",
        source="upload",
    )
    hints2 = draft_usability_hints([broken])
    assert any("_files" in h or "相对资源" in h for h in hints2)

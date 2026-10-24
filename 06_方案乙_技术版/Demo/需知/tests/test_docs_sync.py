"""文档跟着代码走：代码里读的环境变量 .env.example 要列出，REST 接口、MCP 工具、企微命令 README 要写到，README 里的知识库计数与数据文件一致。

只查「代码有、文档要有」这一边：文档写在前、代码后合并时不误报；新增配置或接口忘了写文档时就会红。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")

# 代码读环境变量的几种写法：os.getenv / os.environ.get / os.environ[...]，以及企微 config 里的 _env* 小工具
_ENV_READ = re.compile(r"""(?:os\.getenv|os\.environ\.get|os\.environ\[|\b_env(?:_int|_float|_set)?\()\s*\(?\s*["']([A-Z][A-Z0-9_]+)["']""")
# 不写进 .env.example 的：企微长连接协议参数（默认即可）与 git 自带变量
_ENV_UNDOCUMENTED = {"WECOM_WS_URL", "WECOM_PING_INTERVAL", "WECOM_STREAM_MIN_INTERVAL", "GIT_SSL_NO_VERIFY"}


def _code_files():
    yield from sorted(ROOT.glob("*.py"))   # app / api / mcp_server / run_demo …
    for sub in ("xuzhi", "tools"):          # 不扫 tests（会改环境变量）和 .venv
        yield from sorted((ROOT / sub).rglob("*.py"))


def _env_vars_read() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in _code_files():
        for name in _ENV_READ.findall(path.read_text(encoding="utf-8")):
            found.setdefault(name, path.relative_to(ROOT).as_posix())
    return found


def test_env_example_lists_every_env_var_the_code_reads():
    found = _env_vars_read()
    assert {"LLM_API_KEY", "XUZHI_LEDGER_DB", "WECOM_BOT_ID"} <= set(found)   # 抽取规则本身没坏
    documented = set(re.findall(r"^#\s*([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE, flags=re.M))
    missing = {name: where for name, where in found.items() if name not in documented and name not in _ENV_UNDOCUMENTED}
    assert not missing, f".env.example 缺这些配置（变量 → 读它的文件）：{missing}"


def test_readme_mentions_every_rest_route():
    source = (ROOT / "api.py").read_text(encoding="utf-8")
    routes = re.findall(r"""@app\.(get|post|put|patch|delete)\(\s*["']([^"']+)["']""", source)
    assert len(routes) >= 10
    missing = [f"{verb.upper()} {path}" for verb, path in routes
               if re.sub(r"\{[a-z_]*id\}", "{id}", path) not in README]
    assert not missing, f"README「接口」没写到：{missing}"


def test_readme_mentions_every_mcp_tool_and_resource():
    source = (ROOT / "mcp_server.py").read_text(encoding="utf-8")
    tools = re.findall(r"@mcp\.tool\([^)]*\)\s*\ndef (\w+)", source)
    resources = re.findall(r"""@mcp\.resource\(\s*["']([^"']+)["']""", source)
    assert len(tools) >= 8 and resources
    missing = [name for name in tools + resources if name not in README]
    assert not missing, f"README「MCP」没写到：{missing}"


def test_readme_mentions_every_wecom_command_in_help():
    from xuzhi.channels.wecom.render import HELP_TEXT

    # 命令前面是行首、空白、反引号或「：·（」；「品种/系统」这类斜杠不算命令
    commands = set(re.findall(r"(?<![^\s`：·（(])(/[A-Za-z一-鿿]+)", HELP_TEXT))
    assert {"/help", "/reset", "/问"} <= commands
    missing = sorted(c for c in commands if f"`{c}" not in README)
    assert not missing, f"README「企微入口」没写到这些命令：{missing}"


def test_readme_counts_match_knowledge_and_sample_files():
    systems = json.loads((ROOT / "knowledge" / "system_catalog.json").read_text(encoding="utf-8"))["systems"]
    domains = sum(len(s.get("data_domains") or []) for s in systems)
    interfaces = sum(len(s.get("interfaces") or []) for s in systems)
    assert f"{len(systems)} 个虚构系统 · {domains} 个数据域 · {interfaces} 个接口" in README

    history = json.loads((ROOT / "data" / "history_requirements.json").read_text(encoding="utf-8"))["items"]
    assert f"历史需求库（{len(history)} 条虚构" in README

    samples = [p for p in (ROOT / "data" / "samples").iterdir() if p.suffix == ".md"]
    numerals = "一二三四五六七八九十"
    assert f"{numerals[len(samples) - 1]}个样例" in README

    cases = json.loads((ROOT / "data" / "eval" / "eval_set.json").read_text(encoding="utf-8"))["cases"]
    depts = {c.get("dept", "") for c in cases}
    assert f"eval_set.json`（{len(cases)} 条虚构需求" in README and f"{len(depts)} 类提出方" in README

"""页面底稿 + Git 仓库代码：上传 HTML、HTTP(S) 链接、GitHub/Gitee 文件或整仓。

- HTML 底稿 → 出样复刻改页
- Git 仓库 → 收集前后端源码摘要，送给模型辅助问清 / 定架 / 出样
"""
from __future__ import annotations

import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

MAX_FILE_BYTES = 400_000
MAX_HTML_DRAFTS = 12
# 单文件进内存时截断，避免个别超大文件撑爆内存；源文件数量不设上限
MAX_INGEST_CHARS = 120_000
FETCH_TIMEOUT = 25
CLONE_TIMEOUT = 90

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H_RE = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.I | re.S)
_TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

SKIP_DIR_NAMES = {
    ".git", ".svn", ".hg", ".idea", ".vscode", ".venv", "venv", "node_modules",
    "dist", "build", "out", "target", "coverage", "__pycache__", ".next",
    ".nuxt", "vendor", "bin", "obj", ".turbo", ".cache", "eggs", ".tox",
}
SKIP_FILE_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock",
    "cargo.lock", "poetry.lock", "npm-shrinkwrap.json",
}

FRONTEND_EXT = {".html", ".htm", ".vue", ".tsx", ".jsx", ".ts", ".js", ".css", ".scss", ".less", ".svelte"}
BACKEND_EXT = {".py", ".java", ".go", ".rs", ".php", ".cs", ".kt", ".rb", ".sql", ".scala"}
CONFIG_EXT = {".yml", ".yaml", ".toml", ".json", ".md", ".xml", ".gradle", ".properties"}
CODE_EXT = FRONTEND_EXT | BACKEND_EXT | CONFIG_EXT

ENTRY_HINTS = (
    "readme", "package.json", "pyproject.toml", "requirements.txt", "pom.xml",
    "go.mod", "cargo.toml", "dockerfile", "main.", "app.", "index.", "router",
    "routes", "controller", "service", "api/", "views/", "pages/", "components/",
    "models/", "schema", "openapi", "swagger",
)


@dataclass
class Draft:
    """可出样的 HTML 页面底稿。"""
    name: str
    source: str                 # upload / url / git
    html: str
    title: str = ""
    headings: list[str] = field(default_factory=list)
    table_headers: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def keywords(self) -> set[str]:
        blob = " ".join([self.name, self.title, *self.headings, *self.table_headers])
        return {t for t in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}", blob)}


@dataclass
class CodeFile:
    path: str
    kind: str                   # frontend / backend / html / config
    text: str
    note: str = ""


@dataclass
class RepoBundle:
    """一次 git / 单文件代码拉取的仓库上下文。"""
    url: str
    tree: list[str] = field(default_factory=list)
    files: list[CodeFile] = field(default_factory=list)
    note: str = ""
    ref: str = ""  # 分支/tag/commit；空=仓库默认分支
    scanned: int = 0  # 仓内扫描到的源文件数（含未全文收录的）

    @property
    def name(self) -> str:
        return self.url.rstrip("/").split("/")[-1] or self.url


def _strip_tags(s: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub("", s or "")).strip()


def _kind_for(path: str) -> str:
    ext = Path(path).suffix.lower()
    name = Path(path).name.lower()
    if ext in {".html", ".htm"}:
        return "html"
    if ext in FRONTEND_EXT:
        return "frontend"
    if ext in BACKEND_EXT:
        return "backend"
    if name in ("dockerfile", "makefile") or ext in CONFIG_EXT:
        return "config"
    return "other"


def is_visual_html(html_src: str) -> bool:
    """静态 HTML 是否有可复刻的界面。SPA 入口（空 #app + 一堆 script）不算。"""
    s = html_src or ""
    if not s.strip():
        return False
    if _is_iconfont_catalog(s):
        return False
    stripped = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", s)
    stripped = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", stripped)
    stripped = re.sub(r"(?is)<link\b[^>]*>", " ", stripped)
    stripped = re.sub(r"(?is)<noscript\b[^>]*>.*?</noscript>", " ", stripped)
    visible = _WS_RE.sub(" ", _TAG_RE.sub(" ", stripped)).strip()
    if re.search(r"(?is)<table\b", s) and re.search(r"(?is)<t[hd]\b", s):
        return True
    if re.search(r"(?is)<svg\b", s) or re.search(r"(?is)<canvas\b", s):
        return True
    controls = len(re.findall(r"(?is)<(input|select|button|textarea)\b", stripped))
    headings = len(re.findall(r"(?is)<h[1-6]\b", stripped))
    spa_mount = bool(re.search(r"""id\s*=\s*['"](?:app|root|__next)['"]""", s, re.I))
    script_n = len(re.findall(r"(?is)<script\b", s))
    if len(visible) >= 160 and (controls >= 2 or headings >= 2):
        return True
    if spa_mount and len(visible) < 160:
        return False
    if script_n >= 2 and len(visible) < 80:
        return False
    return len(visible) >= 200


def _is_iconfont_catalog(html_src: str, name: str = "") -> bool:
    """iconfont 演示页（Unicode / Font class / Symbol 列表）不是业务界面。"""
    name_l = (name or "").replace("\\", "/").lower()
    if re.search(r"(?:^|/)(?:demo_index|iconfont|icon-?demo)[^/]*\.(?:html?|htm)$", name_l):
        return True
    if "iconfont" in name_l and name_l.endswith((".html", ".htm", ".css")):
        return True
    head = (html_src or "")[:12000]
    low = head.lower()
    if "unicode font class symbol" in re.sub(r"\s+", " ", low):
        return True
    # 私用区实体 &#xe640; 一类：演示页会堆几十上百个
    entities = len(re.findall(r"&#x[eEfF][0-9a-fA-F]{2,4};", head))
    if entities >= 15:
        real_ui = len(re.findall(r"(?is)<(table|form|input|select|textarea)\b", head))
        if real_ui < 2:
            return True
    if entities >= 8 and re.search(r"icon[_-]?name|font-class|icon_lists|unicode", low):
        return True
    return False


def visual_drafts(drafts: list[Draft] | None) -> list[Draft]:
    """可复刻底稿。用户主动上传/示例：只要不是 SPA 空壳就保留（哪怕内容较短）。"""
    out: list[Draft] = []
    for d in drafts or []:
        html_src = d.html or ""
        if _is_iconfont_catalog(html_src, d.name or ""):
            continue
        if is_visual_html(html_src):
            out.append(d)
            continue
        if d.source not in ("upload", "sample"):
            continue
        if not html_src.strip():
            continue
        stripped = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", html_src)
        stripped = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", stripped)
        visible = _WS_RE.sub(" ", _TAG_RE.sub(" ", stripped)).strip()
        spa_mount = bool(re.search(r"""id\s*=\s*['"](?:app|root|__next)['"]""", html_src, re.I))
        if spa_mount and len(visible) < 80:
            continue
        if len(visible) >= 4 or re.search(r"(?is)<(h[1-6]|p|table|form|nav|main)\b", html_src):
            out.append(d)
    return out


def draft_usability_hints(drafts: list[Draft] | None) -> list[str]:
    """上传/抓取后给出可读提示：空壳、缺资源目录等。"""
    hints: list[str] = []
    for d in drafts or []:
        html_src = d.html or ""
        name = d.name or "底稿"
        if not is_visual_html(html_src):
            if _is_iconfont_catalog(html_src, name):
                hints.append(
                    f"「{name}」是图标字体演示页（iconfont），不是业务界面，已忽略。"
                    "请上传「因子产品表现」等真实页面 HTML，或修好 Git Token 后拉仓库里的 Vue 页面。"
                )
            else:
                hints.append(
                    f"「{name}」几乎没有静态可见内容（常见于 Vue/React「另存为」只存到空 #app）。"
                    "浏览器里看到的界面是 JS 画出来的，单文件 HTML 带不进需知。"
                    "请用开发者工具复制已渲染节点，或上传含完整标签/表格的静态页。"
                )
            continue
        # 另存为「网页，全部」会引用 xxx_files/，只上传单个 html 时样式/图全断
        if re.search(r"""(?:src|href)\s*=\s*['"][^'"]*_files/""", html_src, re.I) or re.search(
            r"""(?:src|href)\s*=\s*['"]\./[^'"]+\.(?:css|js|woff2?|png|jpg)""", html_src, re.I
        ):
            hints.append(
                f"「{name}」引用了本地相对资源（如 xxx_files/ 或 ./xxx.css）。"
                "目前只能上传单个 HTML，配套文件夹上不来，预览会空白或没有样式。"
                "请把关键 CSS 内联进 HTML，或改用「复制已渲染 DOM」方式导出。"
            )
    return hints[:4]


def parse_draft(name: str, html: str, source: str = "upload", note: str = "") -> Draft:
    text = html or ""
    if len(text.encode("utf-8", errors="ignore")) > MAX_FILE_BYTES:
        text = text.encode("utf-8", errors="ignore")[:MAX_FILE_BYTES].decode("utf-8", errors="ignore")
    title_m = _TITLE_RE.search(text)
    title = _strip_tags(title_m.group(1)) if title_m else ""
    headings = [_strip_tags(m.group(1)) for m in _H_RE.finditer(text)]
    headings = [h for h in headings if h][:12]
    headers = [_strip_tags(m.group(1)) for m in _TH_RE.finditer(text)]
    headers = [h for h in headers if h][:40]
    if not title:
        title = headings[0] if headings else Path(name).stem
    return Draft(name=name, source=source, html=text, title=title, headings=headings, table_headers=headers, note=note)


def draft_from_bytes(name: str, data: bytes, source: str = "upload") -> Draft:
    raw = data or b""
    if len(raw) > MAX_FILE_BYTES:
        raw = raw[:MAX_FILE_BYTES]
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            text = ""
    else:
        text = raw.decode("utf-8", errors="ignore")
    return parse_draft(name or "upload.html", text, source=source)


def _decode_bytes(data: bytes) -> str:
    raw = data or b""
    if len(raw) > MAX_FILE_BYTES:
        raw = raw[:MAX_FILE_BYTES]
    for enc in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def _github_raw(url: str) -> str | None:
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)", url)
    if m:
        return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}/{m.group(4)}"
    m = re.match(r"https?://gitee\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)", url)
    if m:
        return f"https://gitee.com/{m.group(1)}/{m.group(2)}/raw/{m.group(3)}/{m.group(4)}"
    if re.match(r"https?://raw\.githubusercontent\.com/.+", url):
        return url
    return None


def _looks_like_repo(url: str) -> bool:
    """判断是否应按 git clone 拉取（含内网 / 自建 GitLab）。"""
    u = (url or "").strip().rstrip("/")
    if not u:
        return False
    if u.startswith("git@"):
        return True
    if u.endswith(".git"):
        return True
    if re.match(r"https?://(github\.com|gitee\.com|gitlab\.com)/[^/]+/[^/]+/?$", u):
        return True
    if re.match(r"https?://(github\.com|gitee\.com|gitlab\.com)/[^/]+/[^/]+/tree/", u):
        return True
    # 自建 / 内网：http(s)://host/group/repo…；允许 /-/tree/分支 浏览链
    nu = normalize_url(u)
    parsed = urlparse(nu)
    if parsed.scheme in ("http", "https") and parsed.hostname and parsed.path:
        path = parsed.path.strip("/")
        if "/-/" in path:
            proj = path.split("/-/", 1)[0]
            if proj.count("/") >= 1:
                return True
        if path.count("/") >= 1 and not Path(path.split("/")[-1]).suffix:
            if not re.search(
                r"(?:^|/)(?:blob|raw|commits?|issues|pulls?|merge_requests|wiki|releases)(?:/|$)",
                path,
                re.I,
            ):
                return True
    return False


def _parse_repo_browse(url: str) -> tuple[str, str, str]:
    """从仓库/浏览 URL 解析 (repo_https无.git, branch或空, 子目录)。

    支持 GitLab ``/-/tree|blob/ref/...``、GitHub/Gitee ``/tree|blob/ref/...``。
    branch 为空表示用远端默认分支。
    """
    u = normalize_url(url or "").rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    if u.startswith("git@"):
        # git@host:group/repo
        m = re.match(r"git@([^:]+):(.+)$", u)
        if m:
            return f"https://{m.group(1)}/{m.group(2).rstrip('/')}", "", ""
        return u, "", ""

    # GitLab：…/group/…/repo/-/(tree|blob|raw|commits)/REF[/path]
    m = re.match(
        r"^(https?://[^/]+/.+?)/-/(?:tree|blob|raw|commits)/([^/]+)(?:/(.*))?$",
        u,
        re.I,
    )
    if m:
        return m.group(1).rstrip("/"), m.group(2), (m.group(3) or "").strip("/")

    # GitHub / Gitee / 部分 GitLab 旧式：host/owner/repo/(tree|blob)/REF[/path]
    m = re.match(
        r"^(https?://(?:github\.com|gitee\.com|gitlab\.com)/[^/]+/[^/]+?)(?:\.git)?"
        r"/(?:tree|blob)/([^/]+)(?:/(.*))?$",
        u,
        re.I,
    )
    if m:
        return m.group(1).rstrip("/"), m.group(2), (m.group(3) or "").strip("/")

    # 纯仓库地址（可能带嵌套组）
    return u, "", ""


def _repo_clone_url(url: str) -> tuple[str, str, str]:
    """返回 (clone_url, branch, subdir)。branch 空=默认分支。"""
    raw = (url or "").strip()
    if raw.startswith("git@"):
        repo, branch, sub = _parse_repo_browse(raw)
        if repo.startswith("https://"):
            return repo + ".git", branch, sub
        return raw, branch, sub
    repo, branch, sub = _parse_repo_browse(raw)
    if not repo:
        return raw, "", ""
    clone = repo if repo.endswith(".git") else repo + ".git"
    return clone, branch, sub


def inject_git_auth(clone_url: str, token: str = "", username: str = "") -> str:
    """把 token 写进 http(s) clone URL。

    - GitHub：固定 ``x-access-token:TOKEN``（页面上的「用户名」会被忽略，避免填错账号导致 401）。
    - Gitee / GitLab / 自建：优先用填写的用户名，否则 ``oauth2:TOKEN``。
    """
    token = (token or "").strip()
    if not token:
        return clone_url
    parsed = urlparse(clone_url)
    if parsed.scheme not in ("http", "https"):
        return clone_url
    # 已有账密则不覆盖（例如链接里自带）
    if parsed.username or parsed.password:
        return clone_url
    host = (parsed.hostname or "").lower()
    is_github = host == "github.com" or host.endswith(".github.com")
    user = (username or "").strip()
    if is_github:
        user = "x-access-token"
    elif not user:
        user = "oauth2"
    auth = f"{quote(user, safe='')}:{quote(token, safe='')}"
    hostport = parsed.hostname or ""
    if parsed.port:
        hostport = f"{hostport}:{parsed.port}"
    netloc = f"{auth}@{hostport}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def redact_secrets(text: str, *secrets: str) -> str:
    """错误信息里去掉 token，避免黄字警告泄露。"""
    out = str(text or "")
    for s in secrets:
        s = (s or "").strip()
        if len(s) >= 4:
            out = out.replace(s, "***")
    out = re.sub(r"(https?://)([^/@\s]+):([^/@\s]+)@", r"\1***:***@", out)
    return out


def normalize_url(url: str) -> str:
    """任意链接缺协议时统一补 https://（已有 http(s)/git@/ssh 则不动）。"""
    u = (url or "").strip()
    if not u:
        return u
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        return u
    if u.startswith("git@"):
        return u
    return "https://" + u.lstrip("/")


def fetch_url(url: str) -> bytes:
    url = normalize_url(url)
    why = check_clone_url(url)
    if why:
        raise RuntimeError(why)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "XuZhi-DraftFetcher/0.3", "Accept": "*/*"},
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        data = resp.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        data = data[:MAX_FILE_BYTES]
    return data


def _should_skip_path(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.relative_to(root).parts
    except ValueError:
        rel_parts = path.parts
    return any(p in SKIP_DIR_NAMES or p.startswith(".") for p in rel_parts[:-1])


def _should_skip_file(path: Path) -> bool:
    name = path.name.lower()
    if name in SKIP_FILE_NAMES:
        return True
    if name.endswith((".min.js", ".min.css", ".map", ".woff", ".woff2", ".ttf", ".eot", ".ico")):
        return True
    # iconfont 资源本身对需求分析几乎无用
    if "iconfont" in name and path.suffix.lower() in {".css", ".js", ".json", ".svg"}:
        return True
    return False


def _file_priority(rel: str) -> int:
    low = rel.lower().replace("\\", "/")
    score = 0
    for h in ENTRY_HINTS:
        if h in low:
            score += 5
    ext = Path(low).suffix
    if ext in {".html", ".htm", ".vue", ".tsx", ".jsx"}:
        score += 4
    elif ext in {".py", ".java", ".go", ".ts", ".js"}:
        score += 3
    elif ext in CONFIG_EXT:
        score += 2
    depth = low.count("/")
    score -= min(depth, 6)
    return score


def _collect_source_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if _should_skip_path(p, root) or _should_skip_file(p):
            continue
        if p.suffix.lower() not in CODE_EXT and p.name.lower() not in ("dockerfile", "makefile"):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        if is_secret_path(str(p.relative_to(root))):
            continue   # 凭据类文件不读
        found.append(p)
    found.sort(key=lambda p: (-_file_priority(str(p.relative_to(root)).replace("\\", "/")), str(p)))
    return found


def _trim_ingest_text(text: str) -> str:
    s = text or ""
    if len(s) <= MAX_INGEST_CHARS:
        return s
    return s[: MAX_INGEST_CHARS - 24] + "\n/* …单文件过长已截断… */\n"


def _build_tree(root: Path, files: list[Path]) -> list[str]:
    entries: list[str] = []
    seen_dirs: set[str] = set()
    for p in files:
        rel = str(p.relative_to(root)).replace("\\", "/")
        parent = str(Path(rel).parent).replace("\\", "/")
        if parent and parent != "." and parent not in seen_dirs:
            seen_dirs.add(parent)
            entries.append(parent + "/")
        entries.append(rel)
    return entries


def ingest_repo_dir(root: Path, url: str = "") -> tuple[list[Draft], RepoBundle]:
    """从本地目录抽出 HTML 底稿 + 前后端代码包。
    只有可复刻的静态 HTML 才进 drafts；看不见界面的空壳页只进代码摘要，不当「已有页面」。
    源文件数量不设上限（仍跳过 node_modules 等噪音目录）。"""
    sources = _collect_source_files(root)
    drafts: list[Draft] = []
    code_files: list[CodeFile] = []
    shell_n = 0
    for p in sources:
        rel = str(p.relative_to(root)).replace("\\", "/")
        try:
            text = _trim_ingest_text(_decode_bytes(p.read_bytes()))
        except OSError:
            continue
        kind = _kind_for(rel)
        if kind == "html" and len(drafts) < MAX_HTML_DRAFTS:
            if _is_iconfont_catalog(text, rel):
                code_files.append(CodeFile(path=rel, kind="html", text=text, note="iconfont"))
                continue
            d = parse_draft(rel, text, source="git")
            if is_visual_html(d.html):
                drafts.append(d)
            else:
                shell_n += 1
                code_files.append(CodeFile(path=rel, kind="html", text=text, note="spa-shell"))
                continue
        code_files.append(CodeFile(path=rel, kind=kind, text=text))

    scanned = len(sources)
    retained = len(code_files)
    note = f"已读全文 {retained} 个源文件 / {len(drafts)} 个可复刻 HTML"
    if shell_n:
        note += f" / {shell_n} 个空壳入口已忽略"
    bundle = RepoBundle(
        url=url or str(root),
        tree=_build_tree(root, sources),
        files=code_files,
        note=note,
        scanned=scanned,
    )
    return drafts, bundle


def _is_private_host(host: str) -> bool:
    """回环 / 内网 / 链路本地地址一律拒绝：仓库只允许拉公网或公司代码托管平台，防止把需知当内网探针。"""
    import ipaddress
    import socket

    h = (host or "").strip().lower().strip("[]")
    if not h or h in ("localhost", "localhost.localdomain") or h.endswith(".local") or h.endswith(".localhost"):
        return True
    candidates: list[str] = []
    try:
        ipaddress.ip_address(h)
        candidates.append(h)
    except ValueError:
        try:
            candidates = sorted({ai[4][0] for ai in socket.getaddrinfo(h, None)})
        except (socket.gaierror, OSError):
            return False   # 解析不了就交给 git 报错，不在此处误判
    for ip in candidates:
        try:
            addr = ipaddress.ip_address(ip.split("%")[0])
        except ValueError:
            return True
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast or addr.is_unspecified:
            return True
    return False


def check_clone_url(clone_url: str) -> str:
    """仓库地址白名单：只放行 http(s)，拒绝 file:// ssh:// git@ 与内网地址。返回不通过的原因，空串为通过。
    允许的协议与"是否拒内网"来自 sandbox.yaml（repo_fetch）。"""
    from . import sandbox

    parsed = urlparse(clone_url)
    allowed = [str(s).lower() for s in (sandbox.get("repo_fetch.allowed_schemes") or ["http", "https"])]
    if (parsed.scheme or "").lower() not in allowed:
        return f"仓库地址只支持 {' / '.join(s + '://' for s in allowed)}（不支持 file://、ssh、git@）"
    if parsed.username or parsed.password:
        return "仓库地址里不要带用户名 / 密码，Token 请填在右侧输入框"
    if sandbox.get("repo_fetch.deny_private_networks", True) and _is_private_host(parsed.hostname or ""):
        return "仓库地址指向本机 / 内网地址，需知不拉取内网资源"
    return ""


def _git_auth_env(token: str = "", username: str = "", host: str = "") -> dict[str, str]:
    """Token 不进命令行、不进 .git/config：通过 GIT_CONFIG_* 环境变量以 Authorization 头传给 git（git ≥ 2.31）。"""
    import base64

    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}
    token = (token or "").strip()
    if not token:
        return env
    user = (username or "").strip()
    h = (host or "").lower()
    if h == "github.com" or h.endswith(".github.com"):
        user = "x-access-token"
    elif not user:
        user = "oauth2"
    basic = base64.b64encode(f"{user}:{token}".encode("utf-8")).decode("ascii")
    env.update({
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    })
    return env


def _is_ssl_handshake_error(detail: str) -> bool:
    low = (detail or "").lower()
    return (
        "schannel" in low
        or "ssl/tls" in low
        or ("ssl" in low and "handshake" in low)
        or "certificate verify failed" in low
        or "unable to get local issuer" in low
        or "ssl routines" in low
    )


def _run_git_clone(
    clone_url: str,
    dest: Path,
    *,
    ssl_no_verify: bool,
    ssl_backend: str | None,
    branch: str = "",
    token: str = "",
    username: str = "",
) -> None:
    env = _git_auth_env(token, username, urlparse(clone_url).hostname or "")
    git_args = [
        "git",
        "-c", "protocol.allow=never",
        "-c", "protocol.http.allow=always",
        "-c", "protocol.https.allow=always",
        "-c", "credential.helper=",
    ]
    if ssl_backend:
        git_args += ["-c", f"http.sslBackend={ssl_backend}"]
    if ssl_no_verify:
        git_args += ["-c", "http.sslVerify=false"]
        env["GIT_SSL_NO_VERIFY"] = "true"
    git_args += ["clone", "--depth", "1"]
    if (branch or "").strip():
        git_args += ["--branch", branch.strip(), "--single-branch"]
    git_args += [clone_url, str(dest)]
    subprocess.run(
        git_args,
        check=True,
        capture_output=True,
        text=True,
        timeout=CLONE_TIMEOUT,
        env=env,
    )


def _gitlab_api_target(url: str) -> tuple[str, str, str] | None:
    """从仓库 URL 解析 (api_root, project_path, ref)。GitHub/Gitee 返回 None。ref 可为空。"""
    u = normalize_url(url).rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    parsed = urlparse(u)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if host in ("github.com", "gitee.com") or host.endswith(".github.com"):
        return None
    repo, ref, _sub = _parse_repo_browse(u)
    parsed_repo = urlparse(repo)
    path = (parsed_repo.path or "").strip("/")
    if path.count("/") < 1:
        return None
    root = f"{parsed.scheme}://{parsed.hostname}"
    if parsed.port:
        root += f":{parsed.port}"
    return root, path, ref


def _http_get_bytes(url: str, headers: dict[str, str], *, ssl_no_verify: bool, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl._create_unverified_context() if ssl_no_verify else None
    kwargs: dict = {"timeout": timeout}
    if ctx is not None:
        kwargs["context"] = ctx
    with urllib.request.urlopen(req, **kwargs) as resp:
        return resp.read()


def _load_via_gitlab_archive(
    url: str,
    token: str,
    *,
    ssl_no_verify: bool,
    sub: str = "",
    branch: str = "",
) -> tuple[list[Draft], list[RepoBundle]]:
    """本机 git/schannel 拉不动时，用 GitLab API 下 archive.zip，沿用所选 SSL 校验设置。"""
    target = _gitlab_api_target(url)
    if not target:
        raise RuntimeError("不是可识别的 GitLab 仓库地址，无法走 API 归档兜底")
    api_root, project_path, url_ref = target
    ref = (branch or url_ref or "").strip()
    if not (token or "").strip():
        raise RuntimeError("Git clone 失败后改走 GitLab API 需要 Token（PRIVATE-TOKEN）")
    api_url = f"{api_root}/api/v4/projects/{quote(project_path, safe='')}/repository/archive.zip"
    if ref:
        api_url += f"?sha={quote(ref, safe='')}"
    headers_list = [
        {"User-Agent": "XuZhi-DraftFetcher/0.3", "PRIVATE-TOKEN": token.strip()},
        {"User-Agent": "XuZhi-DraftFetcher/0.3", "Authorization": f"Bearer {token.strip()}"},
    ]
    data: bytes | None = None
    last_err = ""
    for headers in headers_list:
        try:
            data = _http_get_bytes(api_url, headers, ssl_no_verify=ssl_no_verify, timeout=CLONE_TIMEOUT)
            if data[:2] == b"PK" or len(data) > 100:
                break
            last_err = "返回内容不像 zip"
            data = None
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            last_err = f"HTTP {e.code} {body}".strip()
            if e.code in (401, 403):
                continue
            if e.code == 404:
                raise RuntimeError(
                    f"GitLab API 找不到项目「{project_path}」"
                    + (f" 或分支「{ref}」" if ref else "")
                    + "，或 Token 无读权限（Guest 通常不够，需 Reporter+）"
                ) from e
        except Exception as e:
            last_err = str(e)
            data = None
    if not data:
        if "401" in last_err or "Unauthorized" in last_err:
            raise RuntimeError(
                "GitLab Token 无效或未授权（HTTP 401）。"
                "请核对：① 填的是 Git 用 Access Token（不是上方模型 Key）；"
                "② Token 未过期且勾选了 read_api / read_repository；"
                "③ 项目成员角色 ≥ Reporter（Guest 拉不了归档）。"
                f" 详情：{redact_secrets(last_err, token)}"
            )
        if "403" in last_err or "Forbidden" in last_err:
            raise RuntimeError(
                "GitLab 拒绝访问（HTTP 403）。Token 可能有效但项目角色不够，请升到 Reporter+。"
                f" 详情：{redact_secrets(last_err, token)}"
            )
        raise RuntimeError(f"GitLab API 拉取归档失败：{redact_secrets(last_err, token)}")

    with tempfile.TemporaryDirectory(prefix="xuzhi_glapi_") as tmp:
        zpath = Path(tmp) / "repo.zip"
        zpath.write_bytes(data)
        extract_to = Path(tmp) / "extracted"
        extract_to.mkdir()
        try:
            with zipfile.ZipFile(zpath) as zf:
                zf.extractall(extract_to)
        except zipfile.BadZipFile as e:
            raise RuntimeError("GitLab API 返回的不是有效 zip（可能是登录页/权限错误页）") from e
        # 归档通常多一层 项目名-sha/ 目录
        kids = [p for p in extract_to.iterdir() if p.is_dir()]
        root = kids[0] if len(kids) == 1 else extract_to
        if sub:
            cand = root / sub
            if cand.exists():
                root = cand
        drafts, bundle = ingest_repo_dir(root, url=url)
        if not drafts and not bundle.files:
            raise RuntimeError(f"归档里没有可识别的前端/后端源码：{url}")
        auth = "已带账号" if token.strip() else "匿名"
        ref_note = f"分支 {ref}" if ref else "默认分支"
        bundle.ref = ref
        bundle.note = (bundle.note + " · " if bundle.note else "") + f"{auth} · GitLab API归档 · {ref_note}"
        if ssl_no_verify:
            bundle.note += " · SSL未校验"
        return drafts, [bundle]


def load_from_git(
    url: str,
    token: str = "",
    username: str = "",
    *,
    ssl_no_verify: bool = False,
    branch: str = "",
) -> tuple[list[Draft], list[RepoBundle]]:
    raw = (url or "").strip()
    if raw.startswith("git@"):
        raise RuntimeError(
            "不支持 SSH 地址（git@…）。请改成 http(s)://主机/组/仓.git，并在旁填写 Token"
            + ("（Gitee/自建还需用户名）" if not (username or "").strip() else "")
            + "。"
        )
    clone_url, url_branch, sub = _repo_clone_url(url)
    why = check_clone_url(clone_url)
    if why:
        raise RuntimeError(f"{why}：{redact_secrets(clone_url, token)}")
    # 表单指定分支优先；否则用 URL 里的 /-/tree/…；再空则拉远端默认分支
    branch = (branch or "").strip() or (url_branch or "").strip()
    display_url = redact_secrets(clone_url, token)
    auth_note = "已带账号" if (token or "").strip() else "匿名"
    if not ssl_no_verify:
        ssl_no_verify = (os.getenv("XUZHI_GIT_SSL_NO_VERIFY") or os.getenv("GIT_SSL_NO_VERIFY") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    attempts: list[tuple[bool, str | None]] = [(ssl_no_verify, None)]
    if ssl_no_verify:
        attempts.append((True, "openssl"))
    else:
        attempts.append((False, "openssl"))

    last_detail = ""
    used_no_verify = ssl_no_verify
    used_backend = ""
    clone_ok = False
    with tempfile.TemporaryDirectory(prefix="xuzhi_git_") as tmp:
        dest = Path(tmp) / "repo"
        for i, (no_verify, backend) in enumerate(attempts):
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            try:
                _run_git_clone(
                    clone_url, dest, ssl_no_verify=no_verify, ssl_backend=backend,
                    branch=branch, token=token, username=username,
                )
                used_no_verify = no_verify
                used_backend = backend or ""
                clone_ok = True
                break
            except FileNotFoundError as e:
                raise RuntimeError("本机未安装 git，无法拉取仓库") from e
            except subprocess.TimeoutExpired:
                last_detail = "timeout"
                used_no_verify = no_verify
                if i >= len(attempts) - 1:
                    break
                continue
            except subprocess.CalledProcessError as e:
                last_detail = redact_secrets((e.stderr or e.stdout or str(e)).strip(), token, username)
                used_no_verify = no_verify
                used_backend = backend or ""
                if not _is_ssl_handshake_error(last_detail) or i >= len(attempts) - 1:
                    break
                continue

        if clone_ok:
            root = dest / sub if sub else dest
            if not root.exists():
                root = dest
            drafts, bundle = ingest_repo_dir(root, url=url)
            if not drafts and not bundle.files:
                raise RuntimeError(f"仓库里没有可识别的前端/后端源码：{url}")
            note_bits = [auth_note]
            ref_note = f"分支 {branch}" if branch else "默认分支"
            note_bits.append(ref_note)
            if used_no_verify:
                note_bits.append("SSL未校验")
            if used_backend:
                note_bits.append(f"sslBackend={used_backend}")
            bundle.ref = branch
            bundle.note = (bundle.note + " · " if bundle.note else "") + " · ".join(note_bits)
            return drafts, [bundle]

    # git 不通时走 API 归档兜底，保持相同的 SSL 校验设置。
    api_err = ""
    try:
        return _load_via_gitlab_archive(
            url, token, ssl_no_verify=ssl_no_verify, sub=sub, branch=branch
        )
    except Exception as e:
        api_err = redact_secrets(str(e), token, username)

    detail = last_detail or "clone failed"
    if _is_ssl_handshake_error(detail):
        hint = (
            "（本机 Git/schannel 无法完成 TLS，已自动改走 GitLab API 归档仍失败。"
            f"API：{api_err or '无详情'}。"
            "请确认 Token 有效且项目角色≥Reporter；或改用左侧上传 HTML/源码包）"
        )
    elif not token:
        hint = "（私有仓请填写 Token；Git 失败后的 API 兜底也需要 Token）"
    else:
        hint = f"（git 失败后 API 兜底也失败：{api_err or '无详情'}）"
    raise RuntimeError(f"拉取 git 仓库失败：{display_url}{hint}：{detail[:240]}")


def load_from_url(
    url: str,
    token: str = "",
    username: str = "",
    *,
    git_only: bool = False,
    ssl_no_verify: bool = False,
    branch: str = "",
) -> tuple[list[Draft], list[RepoBundle]]:
    url = normalize_url(url)
    if not url:
        return [], []
    if _looks_like_repo(url):
        return load_from_git(
            url, token=token, username=username, ssl_no_verify=ssl_no_verify, branch=branch
        )
    if git_only:
        raise RuntimeError(
            "此处只拉 Git 仓库。请填 http(s)://主机/组/仓 或 …/仓.git；"
            "内网自建仓也可。网页请左侧上传 HTML。账号填在本条链接旁的 Token/用户名里"
            + ("（已检测到 Token，但仍不像仓库地址）" if (token or "").strip() else "（当前未识别为仓库地址）")
        )
    parsed = urlparse(url)
    fragment = (parsed.fragment or "").strip()
    fetch_target = url.split("#", 1)[0]
    raw = _github_raw(fetch_target) or fetch_target
    try:
        data = fetch_url(raw)
    except Exception as e:
        raise RuntimeError(redact_secrets(f"拉取链接失败：{raw} → {e}", token)) from e
    name = Path(urlparse(raw).path).name or "remote"
    text = _decode_bytes(data)
    note_bits = []
    if fragment:
        note_bits.append("spa-route:" + fragment)
    if not is_visual_html(text):
        note_bits.append("spa-shell")
    note = " ".join(note_bits)
    ext = Path(name).suffix.lower()
    if ext in {".html", ".htm"} or "<html" in text[:500].lower():
        fname = name if name.endswith((".html", ".htm")) else name + ".html"
        return [parse_draft(fname, text, source="url", note=note)], []
    kind = _kind_for(name)
    drafts: list[Draft] = []
    if kind == "html":
        drafts = [parse_draft(name, text, source="url")]
    bundle = RepoBundle(
        url=url,
        tree=[name],
        files=[CodeFile(path=name, kind=kind or "other", text=text)],
        note="单文件",
    )
    return drafts, [bundle]


def load_urls(
    text: str = "",
    token: str = "",
    username: str = "",
    entries: list[dict] | None = None,
    *,
    git_only: bool = False,
    ssl_no_verify: bool = False,
) -> tuple[list[Draft], list[RepoBundle], list[str]]:
    """拉取多条链接。entries 优先：每项 {url, token?, username?, branch?}；token/branch 均可选。
    git_only=True 时只接受仓库地址（页面入口用）；网页请上传 HTML。
    branch 空：拉仓库默认分支（或 URL 里已带的 /-/tree/分支）。"""
    items: list[dict] = []
    if entries is not None:
        for e in entries:
            u = (e.get("url") or "").strip()
            if not u or u.startswith("#"):
                continue
            items.append(
                {
                    "url": u,
                    "token": (e.get("token") or "").strip(),
                    "username": (e.get("username") or "").strip(),
                    "branch": (e.get("branch") or "").strip(),
                }
            )
    else:
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            items.append(
                {
                    "url": line,
                    "token": (token or "").strip(),
                    "username": (username or "").strip(),
                    "branch": "",
                }
            )

    drafts: list[Draft] = []
    repos: list[RepoBundle] = []
    errors: list[str] = []
    for item in items:
        line = item["url"]
        tok = item["token"]
        user = item["username"]
        br = item.get("branch") or ""
        try:
            d, r = load_from_url(
                line,
                token=tok,
                username=user,
                git_only=git_only,
                ssl_no_verify=ssl_no_verify,
                branch=br,
            )
            room = MAX_HTML_DRAFTS - len(drafts)
            if room > 0:
                drafts.extend(d[:room])
            elif d:
                errors.append(f"未纳入：{line} — HTML 底稿已达上限 {MAX_HTML_DRAFTS}")
            repos.extend(r)
            for draft in d:
                if not is_visual_html(draft.html):
                    errors.append(
                        f"无有效内容：{line} — 返回的是空壳/动态页，无法按该页复刻"
                        + (f"（{draft.note}）" if draft.note else "")
                    )
            if not d and not r:
                errors.append(f"无有效内容：{line} — 未识别到 HTML 或源码")
        except Exception as e:
            errors.append(redact_secrets(f"未读到：{line} — {e}", tok, user))
    return drafts, repos, errors


def pick_draft(drafts: list[Draft], keywords: set[str] | list[str]) -> Draft | None:
    ranked = rank_drafts(drafts, keywords)
    return ranked[0] if ranked else None


def draft_relevance(d: Draft, keywords: set[str] | list[str]) -> int:
    keys = {k for k in keywords if k and len(str(k)) >= 2}
    if not keys:
        return 0
    blob = " ".join([d.name, d.title, *d.headings, *d.table_headers, (d.html or "")[:8000]])
    hit = sum(1 for k in keys if str(k) in blob)
    hit += sum(2 for h in d.table_headers if h in keys)
    hit += sum(2 for h in d.headings if any(str(k) in h for k in keys))
    stem = Path(d.name).stem
    hit += sum(3 for k in keys if str(k) in stem or stem in str(k))
    return hit


def rank_drafts(drafts: list[Draft], keywords: set[str] | list[str]) -> list[Draft]:
    """按与需求关键词相关度排序；相关页在前。"""
    if not drafts:
        return []
    return sorted(drafts, key=lambda d: (-draft_relevance(d, keywords), d.name))


def related_drafts(
    drafts: list[Draft],
    keywords: set[str] | list[str],
    *,
    limit: int = 3,
    allow_unrelated_fallback: bool = False,
) -> list[Draft]:
    """只取与本次需求相关的页面。默认不沾边则返回空（避免误用仓库里无关 HTML）。
    allow_unrelated_fallback=True 时（用户主动上传底稿）可退回排名第一页。"""
    ranked = rank_drafts(drafts, keywords)
    if not ranked:
        return []
    scored = [(d, draft_relevance(d, keywords)) for d in ranked]
    related = [d for d, s in scored if s > 0]
    if related:
        return related[:limit]
    if allow_unrelated_fallback:
        return ranked[:1]
    return []


def summarize(drafts: list[Draft], repos: list[RepoBundle] | None = None) -> str:
    bits = []
    usable = visual_drafts(drafts)
    for d in usable[:6]:
        cols = "、".join(d.table_headers[:6]) or "无表头"
        bits.append(f"页 {d.name}（{d.title or '无标题'}；列：{cols}）")
    skipped = len(drafts or []) - len(usable)
    if skipped > 0:
        bits.append(f"另有 {skipped} 个 HTML 空壳未作底稿")
    for r in (repos or [])[:3]:
        bits.append(f"仓 {r.name}（{r.note or (str(len(r.files)) + ' 个源文件')}）")
    if not bits:
        return ""
    return "；".join(bits)


def materials_report(drafts: list[Draft] | None = None, repos: list[RepoBundle] | None = None) -> list[str]:
    """开工后给用户看的「确实读到了什么」明细。"""
    lines: list[str] = []
    for r in repos or []:
        lines.append(f"✅ 已拉取仓库：{r.url or r.name}")
        if r.note:
            lines.append(f"　摘要：{r.note}")
        ref = (r.ref or "").strip()
        if ref:
            lines.append(f"　分支/标签：{ref}")
        elif r.note and "默认分支" not in (r.note or ""):
            lines.append("　分支/标签：默认分支（链接未指定）")
        scanned = int(getattr(r, "scanned", 0) or 0)
        retained = len(r.files or [])
        if scanned or retained:
            lines.append(f"　收录：全文 {retained or scanned} 个源文件（不设数量上限）")
        kinds: dict[str, int] = {}
        for f in r.files or []:
            kinds[f.kind or "other"] = kinds.get(f.kind or "other", 0) + 1
        if kinds:
            lines.append("　源码分类：" + "、".join(f"{k} {v} 个" for k, v in sorted(kinds.items())))
        # 材料明细里多列一些路径，剩余用总数提示
        show_n = 80
        paths = [f.path for f in (r.files or [])[:show_n]]
        if paths:
            more = f" 等共 {len(r.files)} 个" if len(r.files or []) > show_n else ""
            lines.append("　已读文件：" + "、".join(paths) + more)
        show_t = 40
        tree = [t for t in (r.tree or [])[:show_t]]
        if tree:
            more_t = f" …共 {len(r.tree)} 项" if len(r.tree or []) > show_t else ""
            lines.append("　目录摘取：" + "、".join(tree) + more_t)
    usable = visual_drafts(drafts)
    for d in usable[:8]:
        lines.append(
            f"✅ 已用底稿：{d.name}（来源 {d.source}，标题「{d.title or '无'}」"
            + (f"，表头 {len(d.table_headers)} 列" if d.table_headers else "")
            + "）"
        )
    shells = [d for d in (drafts or []) if d not in usable]
    for d in shells[:4]:
        lines.append(f"⚠️ 已见但未作底稿（空壳/不可见）：{d.name}")
    if not lines:
        lines.append("本次未加载 HTML 底稿或 Git 仓库，仅按需求原话分析。")
    return lines


def _trim_text(text: str, limit: int, mark: str = "…已截断…") -> str:
    s = (text or "").strip()
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 20)] + f"\n/* {mark} */\n"


def _trim_html(html: str, limit: int) -> str:
    s = (html or "").strip()
    if len(s) <= limit:
        return s
    head_m = re.search(r"(?is)(<head\b[^>]*>.*?</head>)", s)
    body_m = re.search(r"(?is)(<body\b[^>]*>)(.*?)(</body>)", s)
    head = head_m.group(1) if head_m else ""
    head_budget = max(1500, limit // 3)
    if len(head) > head_budget:
        # 另存为的页面常带整站内联 CSS；head 也按预算截，不能整段送模型
        head = head[:head_budget] + "\n/* …样式过长已截断… */\n</style></head>"
    if body_m:
        open_b, body, close_b = body_m.group(1), body_m.group(2), body_m.group(3)
        budget = max(2000, limit - len(head) - 80)
        if len(body) > budget:
            body = body[:budget] + "\n<!-- …底稿过长已截断… -->\n"
        return f"<!doctype html><html>{head}{open_b}{body}{close_b}</html>"
    return s[:limit] + "\n<!-- …底稿过长已截断… -->\n"


_SECRET_FILE_RE = re.compile(
    r"(?i)(^|/)(\.env(\..*)?|.*\.(pem|key|p12|pfx|jks|keystore)|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|"
    r".*(secret|credential|password|passwd|token)s?[^/]*)$"
)


def is_secret_path(path: str) -> bool:
    """密钥 / 凭据类文件不进模型，也不进代码包。内置正则 + sandbox.yaml files.deny_read_globs（按文件名 glob）叠加。"""
    norm = (path or "").replace("\\", "/")
    if _SECRET_FILE_RE.search(norm):
        return True
    from fnmatch import fnmatch

    from . import sandbox

    name = norm.rsplit("/", 1)[-1].lower()
    return any(fnmatch(name, str(g).lower()) for g in (sandbox.get("files.deny_read_globs") or []))


def redact_material(text: str, mapping: dict[str, str] | None = None) -> str:
    """材料（底稿 HTML、仓库代码摘录）进模型前也过一遍隐盾；mapping 若给出则累计标签→原文，供输出还原。"""
    from .privacy import Redactor

    r = Redactor().redact(text or "")
    if mapping is not None:
        mapping.update(r.mapping)
    return r.text


def _rank_code_files(files: list[CodeFile], keywords: set[str]) -> list[CodeFile]:
    """按与需求关键词、入口/界面文件相关性排序；纯 API 封装靠后。"""
    keys = {k.lower() for k in keywords if k and len(k) >= 2}

    def score(f: CodeFile) -> int:
        low = (f.path + "\n" + f.text[:3000]).lower()
        s = _file_priority(f.path)
        s += sum(3 for k in keys if k in low)
        # 出样更需要有界面结构的文件，避免 Api.ts 这类纯接口层抢占上下文
        if "<template" in f.text or re.search(r"<(?:table|form|el-|a-table|van-)", f.text[:4000], re.I):
            s += 8
        if re.search(r"(^|/)(api|apis|http|request)s?\.[jt]sx?$", f.path.replace("\\", "/"), re.I):
            s -= 6
        if re.search(r"\bexport\s+default\s+class\s+Api\b", f.text[:2000]):
            s -= 8
        if f.note == "spa-shell":
            s -= 4
        if f.note == "iconfont" or _is_iconfont_catalog(f.text, f.path):
            s -= 20
        return s

    return sorted(files, key=lambda f: (-score(f), f.path))


def draft_context_for_llm(
    drafts: list[Draft] | None = None,
    repos: list[RepoBundle] | None = None,
    *,
    prefer: Draft | None = None,
    html_limit: int = 18000,
    code_limit: int = 28000,
    keywords: set[str] | list[str] | None = None,
    mapping: dict[str, str] | None = None,
) -> dict:
    """问清 / 定架 / 出样共用：HTML 底稿 + Git 前后端代码摘录。
    所有 html / excerpt 都先过隐盾（redact_material）再返回；mapping 给出时累计标签→原文，供调用方还原模型输出。
    凭据类文件（.env、*.pem、*secret* …）直接跳过。"""
    drafts = list(drafts or [])
    repos = list(repos or [])
    keys = set(keywords or [])

    ordered = list(drafts)
    if prefer is not None:
        ordered = [prefer] + [d for d in drafts if d is not prefer and d.name != prefer.name]
    html_briefs = []
    remain_html = html_limit
    for i, d in enumerate(ordered[:4]):
        share = remain_html if i == 0 else min(6000, max(1500, remain_html // max(1, 4 - i)))
        html_briefs.append({
            "name": d.name,
            "source": d.source,
            "title": redact_material(d.title, mapping),
            "headings": [redact_material(h, mapping) for h in d.headings[:8]],
            "table_headers": d.table_headers[:20],
            "html": redact_material(_trim_html(d.html, share), mapping),
        })
        remain_html = max(0, remain_html - len(html_briefs[-1]["html"]))
        if remain_html < 1200:
            break

    code_briefs = []
    remain_code = code_limit
    for repo in repos[:3]:
        ranked = _rank_code_files(repo.files, keys)
        repo_files = []
        for f in ranked:
            if remain_code < 800:
                break
            if is_secret_path(f.path):
                continue
            share = min(4000, remain_code)
            excerpt = redact_material(_trim_text(f.text, share), mapping)
            repo_files.append({"path": f.path, "kind": f.kind, "excerpt": excerpt})
            remain_code -= len(excerpt)
        code_briefs.append({
            "url": repo.url,
            "tree": repo.tree[:80],
            "note": repo.note,
            "files": repo_files,
        })

    return {
        "draft_count": len(drafts),
        "repo_count": len(repos),
        "summary": summarize(drafts, repos),
        "html_drafts": html_briefs,
        "repos": code_briefs,
        # 兼容旧字段名
        "drafts": html_briefs,
    }

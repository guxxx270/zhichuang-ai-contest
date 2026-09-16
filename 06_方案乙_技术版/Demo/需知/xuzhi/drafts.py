"""页面底稿 + Git 仓库代码：上传 HTML、HTTP(S) 链接、GitHub/Gitee 文件或整仓。

- HTML 底稿 → 出样复刻改页
- Git 仓库 → 收集前后端源码摘要，送给模型辅助问清 / 定架 / 出样
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

MAX_FILE_BYTES = 400_000
MAX_HTML_DRAFTS = 12
MAX_CODE_FILES = 24
MAX_TREE_ENTRIES = 200
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


def visual_drafts(drafts: list[Draft] | None) -> list[Draft]:
    """可复刻底稿。用户主动上传/示例：只要不是 SPA 空壳就保留（哪怕内容较短）。"""
    out: list[Draft] = []
    for d in drafts or []:
        html_src = d.html or ""
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
    u = url.rstrip("/")
    if u.endswith(".git"):
        return True
    if re.match(r"https?://(github\.com|gitee\.com|gitlab\.com)/[^/]+/[^/]+/?$", u):
        return True
    if re.match(r"https?://(github\.com|gitee\.com|gitlab\.com)/[^/]+/[^/]+/tree/", u):
        return True
    return False


def _repo_clone_url(url: str) -> tuple[str, str]:
    u = url.strip().rstrip("/")
    m = re.match(
        r"https?://(github\.com|gitee\.com|gitlab\.com)/([^/]+)/([^/]+?)(?:\.git)?(?:/tree/[^/]+/?(.*))?$",
        u,
    )
    if m:
        host, owner, repo, sub = m.group(1), m.group(2), m.group(3), (m.group(4) or "").strip("/")
        return f"https://{host}/{owner}/{repo}.git", sub
    if u.endswith(".git"):
        return u, ""
    return u + ".git", ""


def inject_git_auth(clone_url: str, token: str = "", username: str = "") -> str:
    """把 token 写进 http(s) clone URL。GitHub 用 x-access-token；其余默认 oauth2（GitLab/Gitee/多数自建）。"""
    token = (token or "").strip()
    if not token:
        return clone_url
    parsed = urlparse(clone_url)
    if parsed.scheme not in ("http", "https"):
        return clone_url
    if parsed.username or parsed.password:
        return clone_url
    host = (parsed.hostname or "").lower()
    user = (username or "").strip()
    if not user:
        user = "x-access-token" if host == "github.com" or host.endswith(".github.com") else "oauth2"
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
        if _should_skip_path(p, root):
            continue
        if p.suffix.lower() not in CODE_EXT and p.name.lower() not in ("dockerfile", "makefile"):
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        found.append(p)
    found.sort(key=lambda p: (-_file_priority(str(p.relative_to(root)).replace("\\", "/")), str(p)))
    return found


def _build_tree(root: Path, files: list[Path]) -> list[str]:
    entries: list[str] = []
    seen_dirs: set[str] = set()
    for p in files[:MAX_TREE_ENTRIES]:
        rel = str(p.relative_to(root)).replace("\\", "/")
        parent = str(Path(rel).parent).replace("\\", "/")
        if parent and parent != "." and parent not in seen_dirs:
            seen_dirs.add(parent)
            entries.append(parent + "/")
        entries.append(rel)
        if len(entries) >= MAX_TREE_ENTRIES:
            break
    return entries


def ingest_repo_dir(root: Path, url: str = "") -> tuple[list[Draft], RepoBundle]:
    """从本地目录抽出 HTML 底稿 + 前后端代码包。
    只有可复刻的静态 HTML 才进 drafts；看不见界面的空壳页只进代码摘要，不当「已有页面」。"""
    sources = _collect_source_files(root)
    drafts: list[Draft] = []
    code_files: list[CodeFile] = []
    shell_n = 0
    for p in sources:
        rel = str(p.relative_to(root)).replace("\\", "/")
        try:
            text = _decode_bytes(p.read_bytes())
        except OSError:
            continue
        kind = _kind_for(rel)
        if kind == "html" and len(drafts) < MAX_HTML_DRAFTS:
            d = parse_draft(rel, text, source="git")
            if is_visual_html(d.html):
                drafts.append(d)
            else:
                shell_n += 1
                if len(code_files) < MAX_CODE_FILES:
                    code_files.append(CodeFile(path=rel, kind="html", text=text, note="spa-shell"))
                continue
        if len(code_files) < MAX_CODE_FILES:
            code_files.append(CodeFile(path=rel, kind=kind, text=text))
    note = f"{len(code_files)} 个源文件 / {len(drafts)} 个可复刻 HTML"
    if shell_n:
        note += f" / {shell_n} 个空壳入口已忽略"
    bundle = RepoBundle(
        url=url or str(root),
        tree=_build_tree(root, sources),
        files=code_files,
        note=note,
    )
    return drafts, bundle


def load_from_git(
    url: str,
    token: str = "",
    username: str = "",
) -> tuple[list[Draft], list[RepoBundle]]:
    clone_url, sub = _repo_clone_url(url)
    auth_url = inject_git_auth(clone_url, token=token, username=username)
    display_url = redact_secrets(clone_url, token)
    with tempfile.TemporaryDirectory(prefix="xuzhi_git_") as tmp:
        dest = Path(tmp) / "repo"
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", auth_url, str(dest)],
                check=True,
                capture_output=True,
                text=True,
                timeout=CLONE_TIMEOUT,
                env=env,
            )
        except FileNotFoundError as e:
            raise RuntimeError("本机未安装 git，无法拉取仓库") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"拉取 git 仓库超时：{display_url}") from e
        except subprocess.CalledProcessError as e:
            detail = redact_secrets((e.stderr or e.stdout or str(e)).strip(), token, username)
            hint = ""
            if token:
                hint = "（已带 Token；若仍失败请确认 Token 权限/用户名，Gitee 等常需填用户名）"
            else:
                hint = "（私有仓请填写 Git Token）"
            raise RuntimeError(f"拉取 git 仓库失败：{display_url}{hint}：{detail[:300]}") from e
        root = dest / sub if sub else dest
        if not root.exists():
            root = dest
        drafts, bundle = ingest_repo_dir(root, url=url)
        if not drafts and not bundle.files:
            raise RuntimeError(f"仓库里没有可识别的前端/后端源码：{url}")
        return drafts, [bundle]


def load_from_url(
    url: str,
    token: str = "",
    username: str = "",
    *,
    git_only: bool = False,
) -> tuple[list[Draft], list[RepoBundle]]:
    url = normalize_url(url)
    if not url:
        return [], []
    if _looks_like_repo(url):
        return load_from_git(url, token=token, username=username)
    if git_only:
        raise RuntimeError(
            "此处只拉 Git 仓库（地址建议带 .git）；网页/线上页请用左侧「上传 HTML」，"
            "不要贴浏览器地址（多为空壳）"
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
) -> tuple[list[Draft], list[RepoBundle], list[str]]:
    """拉取多条链接。entries 优先：每项 {url, token?, username?}；token 均可选。
    git_only=True 时只接受仓库地址（页面入口用）；网页请上传 HTML。"""
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
                }
            )
    else:
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            items.append({"url": line, "token": (token or "").strip(), "username": (username or "").strip()})

    drafts: list[Draft] = []
    repos: list[RepoBundle] = []
    errors: list[str] = []
    for item in items:
        line = item["url"]
        tok = item["token"]
        user = item["username"]
        try:
            d, r = load_from_url(line, token=tok, username=user, git_only=git_only)
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
        kinds: dict[str, int] = {}
        for f in r.files or []:
            kinds[f.kind or "other"] = kinds.get(f.kind or "other", 0) + 1
        if kinds:
            lines.append("　源码分类：" + "、".join(f"{k} {v} 个" for k, v in sorted(kinds.items())))
        paths = [f.path for f in (r.files or [])[:10]]
        if paths:
            more = f" 等共 {len(r.files)} 个" if len(r.files or []) > 10 else ""
            lines.append("　已读文件：" + "、".join(paths) + more)
        tree = [t for t in (r.tree or [])[:12]]
        if tree:
            lines.append("　目录摘取：" + "、".join(tree))
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
    if body_m:
        open_b, body, close_b = body_m.group(1), body_m.group(2), body_m.group(3)
        budget = max(2000, limit - len(head) - 80)
        if len(body) > budget:
            body = body[:budget] + "\n<!-- …底稿过长已截断… -->\n"
        return f"<!doctype html><html>{head}{open_b}{body}{close_b}</html>"
    return s[:limit] + "\n<!-- …底稿过长已截断… -->\n"


def _rank_code_files(files: list[CodeFile], keywords: set[str]) -> list[CodeFile]:
    """按与需求关键词、入口文件相关性排序；前后端同等参与，不拆开两套队列。"""
    keys = {k.lower() for k in keywords if k and len(k) >= 2}

    def score(f: CodeFile) -> int:
        low = (f.path + "\n" + f.text[:3000]).lower()
        s = _file_priority(f.path)
        s += sum(3 for k in keys if k in low)
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
) -> dict:
    """问清 / 定架 / 出样共用：HTML 底稿 + Git 前后端代码摘录。"""
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
            "title": d.title,
            "headings": d.headings[:8],
            "table_headers": d.table_headers[:20],
            "html": _trim_html(d.html, share),
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
            share = min(4000, remain_code)
            excerpt = _trim_text(f.text, share)
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

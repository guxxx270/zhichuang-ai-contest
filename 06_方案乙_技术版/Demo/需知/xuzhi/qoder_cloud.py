"""Qoder Cloud Agents 官方 API（非 OpenAI 兼容）。

官方流程：PAT → 环境 → Agent → Session → POST user.message → 轮询 events 直到 idle。
文档：https://docs.qoder.com/zh/cloud-agents/quickstart
国内：https://api.qoder.com.cn ；国际：https://api.qoder.com
"""
from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

AGENT_NAME = "xuzhi-refine"
DEFAULT_MODEL = "ultimate"
AGENT_SYSTEM = (
    "你是期货公司技术部的需求分析师。只根据用户要求组织文字或 JSON，"
    "不要执行命令、不要读写文件、不要调用工具。直接给出最终答案。"
)

# 填 PAT 前的兜底目录（官方文档常见值）。有 PAT 后改拉 GET /api/v1/cloud/models。
CN_MODEL_FALLBACK: list[tuple[str, str]] = [
    ("auto", "auto · 自动选型"),
    ("qwen3.7-max", "qwen3.7-max · 通义旗舰"),
    ("qwen3.7-plus", "qwen3.7-plus · 通义多模态"),
    ("qwen3.6-flash", "qwen3.6-flash · 通义轻量"),
    ("deepseek-v4-pro", "deepseek-v4-pro · DeepSeek 旗舰"),
    ("deepseek-v4-flash", "deepseek-v4-flash · DeepSeek 轻量"),
    ("glm-5.1", "glm-5.1 · 智谱"),
    ("kimi-k2.6", "kimi-k2.6 · Kimi"),
    ("minimax-m2.7", "minimax-m2.7 · MiniMax"),
    ("ultimate", "ultimate · 国际档位名（部分账号也可用）"),
]
INTL_MODEL_FALLBACK: list[tuple[str, str]] = [
    ("ultimate", "Ultimate"),
    ("auto", "auto"),
]
CUSTOM_MODEL_SENTINEL = "__custom_qoder_model__"


def fallback_models(api_base: str) -> list[tuple[str, str]]:
    b = (api_base or "").lower()
    if "qoder.com.cn" in b:
        return list(CN_MODEL_FALLBACK)
    return list(INTL_MODEL_FALLBACK)


def _agent_name_for(model: str) -> str:
    slug = "".join(c if c.isalnum() or c in "._-" else "-" for c in (model or DEFAULT_MODEL).strip())[:48]
    return f"{AGENT_NAME}-{slug or DEFAULT_MODEL}"


def parse_model_catalog(raw: Any) -> list[tuple[str, str]]:
    rows = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in rows:
        if not isinstance(m, dict) or m.get("is_enabled") is False:
            continue
        mid = str(m.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        name = str(m.get("display_name") or mid).strip()
        out.append((mid, f"{name}（{mid}）" if name.lower() != mid.lower() else mid))
    return out


def list_qoder_models(api_base: str, pat: str) -> list[tuple[str, str]]:
    """有 PAT 时拉账号目录；失败则返回文档兜底列表。"""
    fb = fallback_models(api_base)
    if not (pat or "").strip():
        return fb
    try:
        client = QoderCloudClient(api_base=api_base, pat=pat)
        live = parse_model_catalog(client._request("GET", "/models"))
        return live or fb
    except Exception:
        return fb


def _cloud_root(api_base: str) -> str:
    b = (api_base or "").strip().rstrip("/")
    if not b:
        b = "https://api.qoder.com.cn"
    if b.endswith("/api/v1/cloud"):
        return b
    if b.endswith("/api/v1"):
        return b + "/cloud"
    return b + "/api/v1/cloud"


def _text_of(event: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in event.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            t = (block.get("text") or "").strip()
            if t:
                parts.append(t)
        elif isinstance(block, str) and block.strip():
            parts.append(block.strip())
    return "\n".join(parts)


class QoderCloudClient:
    def __init__(
        self,
        api_base: str,
        pat: str,
        model: str = DEFAULT_MODEL,
        agent_id: str = "",
        environment_id: str = "",
        timeout: float = 180.0,
    ) -> None:
        self.root = _cloud_root(api_base)
        self.pat = pat.strip()
        self.model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.agent_id = (agent_id or "").strip()
        self.environment_id = (environment_id or "").strip()
        self.timeout = timeout
        self._session_id = ""

    def _request(self, method: str, path: str, body: dict | None = None, query: dict | None = None) -> Any:
        url = self.root + path
        if query:
            url += "?" + urlencode({k: v for k, v in query.items() if v is not None and v != ""})
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Authorization": f"Bearer {self.pat}", "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
        except HTTPError as e:
            err = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
            raise RuntimeError(f"Qoder Cloud API {e.code} {path}：{err[:800]}") from e
        except URLError as e:
            raise RuntimeError(f"连不上 Qoder Cloud（{self.root}）：{e}") from e
        if not raw.strip():
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}

    def _id(self, obj: Any, *keys: str) -> str:
        if isinstance(obj, dict):
            for k in keys or ("id",):
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            data = obj.get("data")
            if isinstance(data, dict):
                return self._id(data, *keys)
            if isinstance(data, list) and data:
                return self._id(data[0], *keys)
        return ""

    def _list(self, path: str) -> list[dict]:
        obj = self._request("GET", path)
        data = obj.get("data") if isinstance(obj, dict) else None
        return data if isinstance(data, list) else []

    def ensure_environment(self) -> str:
        if self.environment_id:
            return self.environment_id
        items = self._list("/environments")
        if items:
            self.environment_id = self._id(items[0])
        else:
            created = self._request("POST", "/environments", {"name": "default"})
            self.environment_id = self._id(created)
        if not self.environment_id:
            raise RuntimeError("Qoder 没有可用环境，且创建失败。请在控制台先建一个 Environment。")
        return self.environment_id

    def ensure_agent(self) -> str:
        if self.agent_id:
            return self.agent_id
        want = _agent_name_for(self.model)
        for a in self._list("/agents"):
            if a.get("name") == want:
                self.agent_id = self._id(a)
                break
        if not self.agent_id:
            payload = {"name": want, "model": self.model, "tools": []}
            try:
                created = self._request("POST", "/agents", {**payload, "system": AGENT_SYSTEM})
            except RuntimeError:
                created = self._request("POST", "/agents", {**payload, "instructions": AGENT_SYSTEM})
            self.agent_id = self._id(created)
        if not self.agent_id:
            raise RuntimeError("创建 / 查找 Qoder Agent 失败。可在页面填写已有 Agent ID（agent_…）。")
        return self.agent_id

    def ensure_session(self) -> str:
        if self._session_id:
            return self._session_id
        env_id = self.ensure_environment()
        agent_id = self.ensure_agent()
        created = self._request("POST", "/sessions", {
            "agent": agent_id,
            "environment_id": env_id,
            "title": "需知 · 需求润色",
            "metadata": {"app": "xuzhi"},
        })
        self._session_id = self._id(created)
        if not self._session_id:
            raise RuntimeError("创建 Qoder Session 失败。")
        return self._session_id

    def chat(self, system: str, user: str) -> str:
        sid = self.ensure_session()
        prompt = f"{system.strip()}\n\n{user.strip()}".strip()
        sent = self._request("POST", f"/sessions/{sid}/events", {
            "events": [{"type": "user.message", "content": [{"type": "text", "text": prompt}]}],
        })
        after_id = ""
        if isinstance(sent, dict):
            rows = sent.get("data") if isinstance(sent.get("data"), list) else [sent]
            after_id = self._id(rows[0]) if rows else ""
        deadline = time.time() + self.timeout
        texts: list[str] = []
        while time.time() < deadline:
            time.sleep(1.5)
            query = {"limit": "100", "order": "asc"}
            if after_id:
                query["after_id"] = after_id
            page = self._request("GET", f"/sessions/{sid}/events", query=query)
            rows = page.get("data") if isinstance(page, dict) else []
            if not isinstance(rows, list):
                rows = []
            idle = False
            for ev in rows:
                if not isinstance(ev, dict):
                    continue
                typ = ev.get("type") or ""
                if typ == "agent.message":
                    t = _text_of(ev)
                    if t:
                        texts.append(t)
                if typ == "session.status_idle":
                    idle = True
                eid = ev.get("id")
                if isinstance(eid, str) and eid:
                    after_id = eid
            if idle:
                return "\n".join(texts).strip()
        raise RuntimeError("Qoder Cloud Session 超时未回到 idle。请到 Qoder 控制台确认 PAT / 环境 / 额度。")

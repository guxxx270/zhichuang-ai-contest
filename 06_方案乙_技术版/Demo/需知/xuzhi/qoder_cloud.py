"""Qoder Cloud Agents 官方 API（非 OpenAI 兼容）。

官方流程：PAT → 环境 → Agent → Session → POST user.message → 轮询 events 直到 idle。
文档：https://docs.qoder.com/zh/cloud-agents/quickstart
国内：https://api.qoder.com.cn ；国际：https://api.qoder.com
"""
from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

AGENT_NAME = "xuzhi-refine"
DEFAULT_MODEL = "ultimate"
AGENT_SYSTEM = (
    "你是需求分析师。只根据用户要求组织文字或 JSON，"
    "不要执行命令、不要读写文件、不要调用工具。直接给出最终答案。"
)

CUSTOM_MODEL_SENTINEL = "__custom_qoder_model__"


def _agent_name_for(model: str) -> str:
    slug = "".join(c if c.isalnum() or c in "._-" else "-" for c in (model or DEFAULT_MODEL).strip())[:48]
    return f"{AGENT_NAME}-{slug or DEFAULT_MODEL}"


def _model_id_from_obj(m: Any) -> str:
    if isinstance(m, str):
        return m.strip()
    if isinstance(m, dict):
        for k in ("id", "model", "model_id", "name"):
            v = m.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, dict):
                inner = _model_id_from_obj(v)
                if inner:
                    return inner
    return ""


def parse_model_catalog(raw: Any) -> list[tuple[str, str]]:
    rows = raw.get("data") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        return []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in rows:
        if isinstance(m, str):
            mid = m.strip()
            if mid and mid not in seen:
                seen.add(mid)
                out.append((mid, mid))
            continue
        if not isinstance(m, dict):
            continue
        if m.get("is_enabled") is False:
            continue
        mid = _model_id_from_obj(m)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        name = str(m.get("display_name") or m.get("name") or mid).strip()
        tag = ""
        if m.get("is_new"):
            tag = " · new"
        src = str(m.get("source") or "").strip()
        if src and src != "system":
            tag += f" · {src}"
        if name and name.lower() != mid.lower():
            label = f"{name}（{mid}）{tag}"
        elif name and name != mid:
            label = f"{name}（{mid}）{tag}"
        else:
            label = f"{mid}{tag}"
        out.append((mid, label.strip()))
    return out


def merge_model_catalogs(*catalogs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for cat in catalogs:
        for mid, lab in cat or []:
            mid = (mid or "").strip()
            if not mid or mid in seen:
                continue
            seen.add(mid)
            out.append((mid, lab or mid))
    return out


def filter_model_catalog(catalog: list[tuple[str, str]], query: str) -> list[tuple[str, str]]:
    """在实时目录里按 id / 显示名过滤。"""
    q = (query or "").strip().lower()
    if not q:
        return list(catalog)
    hit: list[tuple[str, str]] = []
    soft: list[tuple[str, str]] = []
    for mid, lab in catalog:
        blob = f"{mid} {lab}".lower()
        if q in blob:
            hit.append((mid, lab))
            continue
        compact_q = re.sub(r"[^a-z0-9]", "", q)
        compact_b = re.sub(r"[^a-z0-9]", "", blob)
        if compact_q and compact_q in compact_b:
            soft.append((mid, lab))
            continue
        if compact_q and any(
            compact_q in re.sub(r"[^a-z0-9]", "", x) or re.sub(r"[^a-z0-9]", "", x) in compact_q
            for x in (mid.lower(), lab.lower())
        ):
            soft.append((mid, lab))
    return hit or soft


def list_qoder_models(api_base: str, pat: str) -> tuple[list[tuple[str, str]], str]:
    """只拉账号实时目录 GET /models，不用本地兜底列表。"""
    if not (pat or "").strip():
        return [], "请先填写 PAT，再拉取实时模型目录（与 PyCharm 同源）。"
    try:
        client = QoderCloudClient(api_base=api_base, pat=pat)
        live = parse_model_catalog(client._request("GET", "/models"))
        if live:
            return live, f"实时目录 · {len(live)} 个当前可用模型"
        return [], "账号当前无可用模型（目录为空，可能已下架或未开通）。"
    except Exception as e:
        return [], f"拉取实时目录失败：{e}"


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

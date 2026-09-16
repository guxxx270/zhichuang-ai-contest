"""LLM 客户端：OpenAI 兼容接口 / Qoder Cloud Agents / mock。

- api 模式：页面或 .env 传入的网关；OpenAI 兼容走 /chat/completions，Qoder 走 Cloud Agents。
- mock 模式：不联网，返回空结果，由规则引擎兜底。
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import config


def list_openai_models(api_base: str, api_key: str) -> tuple[list[tuple[str, str]], str]:
    """实时拉取 OpenAI 兼容网关 GET /models（硅基流动等），不用本地写死列表。"""
    from .qoder_cloud import parse_model_catalog

    if not (api_key or "").strip():
        return [], "请先填写 API Key，再拉取实时模型目录。"
    base = (api_base or "").strip().rstrip("/")
    if not base:
        return [], "缺少 API Base。"
    url = base if base.endswith("/models") else base + "/models"
    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return [], f"拉取实时目录失败：HTTP {e.code} {detail}".strip()
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
        return [], f"拉取实时目录失败：{e}"
    live = parse_model_catalog(raw)
    if live:
        return live, f"实时目录 · {len(live)} 个当前可用模型"
    return [], "账号当前无可用模型（目录为空）。"


class LLM:
    def __init__(
        self,
        mode: str | None = None,
        model: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        backend: str = "openai",
        extra: dict[str, str] | None = None,
    ) -> None:
        self.api_base = (api_base if api_base is not None else config.LLM_API_BASE).strip()
        self.api_key = (api_key if api_key is not None else config.LLM_API_KEY).strip()
        self.model = (model if model is not None else config.LLM_MODEL).strip()
        self.backend = (backend or "openai").strip()
        extra = extra or {}
        self.note = ""
        self.last_model = ""
        self._client = None
        self._qoder = None
        if mode in ("api", "mock"):
            self.mode = mode
        elif mode == "auto" or mode is None:
            if self.backend == "qoder-cloud":
                self.mode = "api" if (self.api_key and self.api_base) else "mock"
            else:
                self.mode = "api" if (self.api_key and self.api_base and self.model) else "mock"
        else:
            self.mode = config.resolved_mode()
        if self.mode == "api" and self.backend == "qoder-cloud":
            from .qoder_cloud import QoderCloudClient
            self._qoder = QoderCloudClient(
                api_base=self.api_base,
                pat=self.api_key,
                model=self.model or "ultimate",
                agent_id=extra.get("agent_id", ""),
                environment_id=extra.get("environment_id", ""),
            )
            return
        if self.mode == "api":
            try:
                from openai import OpenAI  # 延迟导入，mock 模式不需要
                self._client = OpenAI(base_url=self.api_base, api_key=self.api_key, timeout=120, max_retries=1)
            except ImportError:
                self.mode, self.note = "mock", "未安装 openai，已退回 mock（pip install openai 后恢复）"

    def chat(self, system: str, user: str, temperature: float = 0.1, json_mode: bool = False) -> str:
        if self.mode == "mock":
            return ""
        if self.backend == "qoder-cloud":
            if not self._qoder:
                return ""
            text = self._qoder.chat(system, user)
            self.last_model = self.model
            return text
        kwargs: dict[str, Any] = dict(
            model=self.model,
            temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:  # 网关不支持 json_object 时退回普通模式
            if json_mode:
                kwargs.pop("response_format", None)
                resp = self._client.chat.completions.create(**kwargs)
            else:
                raise RuntimeError(f"LLM 调用失败：{e}") from e
        self.last_model = getattr(resp, "model", "") or self.model
        return resp.choices[0].message.content or ""

    def chat_json(self, system: str, user: str) -> Any:
        raw = self.chat(system, user, json_mode=True)
        return parse_json_loose(raw)


def parse_json_loose(text: str) -> Any:
    """从模型输出里尽量抠出 JSON（容忍 ```json 围栏与前后废话）。"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = m.group(1) if m else text
    start = min([i for i in (candidate.find("{"), candidate.find("[")) if i >= 0], default=-1)
    if start < 0:
        return None
    candidate = candidate[start:]
    for end in range(len(candidate), 0, -1):
        try:
            return json.loads(candidate[:end])
        except json.JSONDecodeError:
            continue
    return None


def load_prompt(name: str) -> str:
    p = config.PROMPT_DIR / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""

"""LLM 客户端：OpenAI 兼容接口 + mock 模式（沿用研伴）。

- api 模式：任何 OpenAI 兼容网关（公司 AI 平台 / DashScope / DeepSeek / vLLM 等）。
- mock 模式：不联网，返回空结果，由规则引擎兜底，保证整条链路可演示。
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import config


class LLM:
    def __init__(self) -> None:
        self.mode = config.resolved_mode()
        self._client = None
        if self.mode == "api":
            try:
                from openai import OpenAI  # 延迟导入，mock 模式不需要
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("api 模式需要安装 openai：pip install openai") from e
            self._client = OpenAI(base_url=config.LLM_API_BASE, api_key=config.LLM_API_KEY, timeout=90, max_retries=1)

    def chat(self, system: str, user: str, temperature: float = 0.1, json_mode: bool = False) -> str:
        if self.mode == "mock":
            return ""
        kwargs: dict[str, Any] = dict(
            model=config.LLM_MODEL,
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

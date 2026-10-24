"""LLM 客户端：OpenAI 兼容接口 / Qoder Cloud Agents / mock。

- api 模式：页面或 .env 传入的网关；OpenAI 兼容走 /chat/completions，Qoder 走 Cloud Agents。
- mock 模式：不联网，返回空结果，由规则引擎兜底。
- 每次真实调用都记审计（xuzhi/audit.py）：用途、渠道、网关主机、模型、耗时、脱敏标签数、明文敏感项数、成败；
  网关协议受 sandbox.yaml llm.allowed_api_schemes 约束。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import config, sandbox

GATEWAY_COOLDOWN = 120   # 网关超时 / 连不上之后，这么多秒内的调用直接走规则引擎
_JSON_STARTS = 64        # parse_json_loose 每种括号最多试几个起点


class OutputTruncated(RuntimeError):
    """模型输出被 max_tokens 截断（finish_reason=length）：内容不完整，不能当格式错误处理，也不该原样重问。"""


def _gateway_down(e: BaseException | None) -> bool:
    """超时、连不上（含 openai 的 APITimeoutError / APIConnectionError）；HTTP 4xx/5xx 不算。"""
    if e is None or isinstance(e, HTTPError):
        return False
    if isinstance(e, (TimeoutError, ConnectionError, URLError)):
        return True
    try:
        from openai import APIConnectionError   # APITimeoutError 是它的子类
    except ImportError:
        return False
    return isinstance(e, APIConnectionError)


def gateway_unreachable(e: BaseException | None) -> bool:
    """调用方区分「网关超时 / 连不上」与其他失败；chat() 包过一层的异常看它的 __cause__。"""
    return _gateway_down(e) or _gateway_down(getattr(e, "__cause__", None))


def list_openai_models(api_base: str, api_key: str) -> tuple[list[tuple[str, str]], str]:
    """实时拉取 OpenAI 兼容网关 GET /models（硅基流动等），不用本地写死列表。"""
    from .qoder_cloud import parse_model_catalog

    if not (api_key or "").strip():
        return [], "请先填写 API Key，再拉取实时模型目录。"
    base = (api_base or "").strip().rstrip("/")
    if not base:
        return [], "缺少 API Base。"
    url = base if base.endswith("/models") else base + "/models"
    if not re.match(r"^https?://", url, re.I):
        return [], "API Base 须以 http:// 或 https:// 开头。"
    try:
        req = Request(
            url,
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Accept": "application/json",
            },
            method="GET",
        )
        with urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        return [], f"拉取实时目录失败：HTTP {e.code} {detail}".strip()
    except (URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as e:
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
        audit: Any = None,
        channel: str = "",
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
        self.audit = audit            # None = 用默认审计库；False = 不审计（仅测试）
        self.channel = channel        # Web / 企微 / API / MCP
        self.req_id = 0
        self.current_purpose = ""     # 调用方在 chat 前设置（问清润色 / 写单润色 / 出样改稿）
        self._down_until = 0.0        # 网关超时 / 连不上后的冷却截止时刻
        if mode is None:              # 没指定就看 .env / 环境变量：LLM_MODE=mock 强制只走规则引擎
            mode = "auto"
            if config.LLM_MODE == "mock":
                mode, self.note = "mock", "LLM_MODE=mock，强制只走规则引擎"
        if mode in ("api", "mock"):
            self.mode = mode
        elif mode == "auto":
            if self.backend == "qoder-cloud":
                self.mode = "api" if (self.api_key and self.api_base) else "mock"
            else:
                self.mode = "api" if (self.api_key and self.api_base and self.model) else "mock"
        else:
            self.mode = config.resolved_mode()
        if self.mode == "api" and self.api_base:
            scheme = (urlparse(self.api_base).scheme or "").lower()
            allowed = [str(s).lower() for s in (sandbox.get("llm.allowed_api_schemes") or ["https", "http"])]
            if scheme not in allowed:
                self.mode, self.note = "mock", f"网关协议 {scheme or '（空）'} 不在沙箱策略允许范围（{'/'.join(allowed)}），已退回规则引擎"
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
                from openai import OpenAI, Timeout  # 延迟导入，mock 模式不需要
                # 不自动重试：超时再重试只会让页面多卡一轮；失败的这一步直接用规则结果
                self._client = OpenAI(base_url=self.api_base, api_key=self.api_key,
                                      timeout=Timeout(config.LLM_TIMEOUT, connect=10.0), max_retries=0)
            except ImportError:
                self.mode, self.note = "mock", "未安装 openai，已退回 mock（pip install openai 后恢复）"

    def chat(self, system: str, user: str, temperature: float = 0.1, json_mode: bool = False, purpose: str = "") -> str:
        if self.mode == "mock":
            return ""
        if time.time() < self._down_until:   # 网关刚超时 / 连不上：本次其余步骤不再一步步等超时
            raise RuntimeError(self.note)
        purpose = purpose or self.current_purpose
        t0 = time.time()
        try:
            text = self._chat_raw(system, user, temperature, json_mode, purpose)
        except Exception as e:
            self._audit(purpose, system, user, "", t0, ok=False, error=f"{type(e).__name__}: {e}")
            if _gateway_down(e) or _gateway_down(e.__cause__):
                self._down_until = time.time() + GATEWAY_COOLDOWN
                self.note = f"模型网关超时或连不上，{GATEWAY_COOLDOWN} 秒内其余步骤直接走规则引擎"
            raise
        self._audit(purpose, system, user, text, t0, ok=True)
        return text

    def _audit(self, purpose: str, system: str, user: str, text: str, t0: float, *, ok: bool, error: str = "") -> None:
        if self.audit is False or not sandbox.get("llm.audit_every_call", True):
            return
        try:
            from .audit import count_plain_sensitive, count_redaction_tags, default_audit

            sink = self.audit if self.audit is not None else default_audit()
            if sink is None:
                return
            sink.record(
                purpose=purpose, channel=self.channel, backend=self.backend, api_base=self.api_base,
                model=self.last_model or self.model, duration_ms=int((time.time() - t0) * 1000),
                prompt_chars=len(system or "") + len(user or ""), completion_chars=len(text or ""),
                redacted_tags=count_redaction_tags(system, user), plain_hits=count_plain_sensitive(user),
                ok=ok, error=error, req_id=self.req_id,
            )
        except Exception:   # noqa: BLE001  审计失败不影响业务
            pass

    def _chat_raw(self, system: str, user: str, temperature: float, json_mode: bool, purpose: str) -> str:
        if sandbox.get("llm.block_if_plain_sensitive", False):
            from .audit import count_plain_sensitive

            n = count_plain_sensitive(user)
            if n:
                raise RuntimeError(f"沙箱策略拒发：出站提示词含 {n} 处明文敏感项（{purpose or '模型调用'}），请先脱敏")
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
        if config.LLM_MAX_TOKENS > 0:
            kwargs["max_tokens"] = config.LLM_MAX_TOKENS
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as e:  # 网关不支持 json_object 时退回普通模式；超时 / 连不上就别再白等一轮
            if json_mode and not _gateway_down(e):
                kwargs.pop("response_format", None)
                resp = self._client.chat.completions.create(**kwargs)
            else:
                raise RuntimeError(f"LLM 调用失败：{e}") from e
        self.last_model = getattr(resp, "model", "") or self.model
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":   # 半截输出：说清是截断，别让下游报成「格式不对」
            raise OutputTruncated("模型输出被截断（finish_reason=length）")
        return choice.message.content or ""

    def chat_json(self, system: str, user: str, purpose: str = "") -> Any:
        raw = self.chat(system, user, json_mode=True, purpose=purpose)
        return parse_json_loose(raw)


def parse_json_loose(text: str) -> Any:
    """从模型输出里尽量抠出 JSON（容忍 ```json 围栏与前后废话）。
    依次从每个「{」起用 raw_decode 解一个完整对象（前面的说明里带「[见下]」也不碍事），都不行才试「[」；
    截断的 JSON 解不出来就返回 None，不再逐字截短重试（那是 O(n²)，长输出会卡住）。"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = m.group(1) if m else text
    decoder = json.JSONDecoder()
    # 「{」解到一半出错的区间（截断或写坏的大对象）：里面那些完整的小对象 / 列表不单拿出来。区间按起点递增、互不重叠
    failed: list[tuple[int, int]] = []
    for opener in ("{", "["):
        start, k, tries = candidate.find(opener), 0, 0
        # 每种括号最多试 _JSON_STARTS 个起点：正常的说明文字里没这么多括号；每次出错都要从头数行号，起点多了会拖成平方级
        while start >= 0 and tries < _JSON_STARTS:
            while k < len(failed) and failed[k][1] <= start:
                k += 1
            if opener == "[" and k < len(failed) and failed[k][0] <= start:
                start = candidate.find(opener, failed[k][1])
                continue
            tries += 1
            try:
                return decoder.raw_decode(candidate, start)[0]
            except json.JSONDecodeError as exc:
                end = max(start + 1, exc.pos)
            except RecursionError:   # 嵌套深到解析器递归上限，不是正常输出；后面不再逐个起点重试
                end = len(candidate)
            if opener == "{":
                failed.append((start, end))
            start = candidate.find(opener, end)
    return None


def load_prompt(name: str) -> str:
    p = config.PROMPT_DIR / f"{name}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""

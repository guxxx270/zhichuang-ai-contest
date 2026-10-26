"""系统代理的例外清单（127.0.0.1、内网、*.公司域名）对模型调用同样生效，与拉模型目录的 urllib 一致。"""
from __future__ import annotations

import urllib.request

import httpx

from xuzhi.llm import LLM, _bypass_proxy_mounts


def _system_proxy(monkeypatch, bypassed: set[str]):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"})
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: host in bypassed)


def test_bypassed_gateway_gets_direct_mount(monkeypatch):
    _system_proxy(monkeypatch, {"ai.example-intranet.cn", "127.0.0.1", "::1"})
    assert _bypass_proxy_mounts("https://ai.example-intranet.cn/v1") == {"all://ai.example-intranet.cn": None}
    assert _bypass_proxy_mounts("http://127.0.0.1:8781/v1") == {"all://127.0.0.1": None}
    assert _bypass_proxy_mounts("http://[::1]:8781/v1") == {"all://[::1]": None}


def test_proxied_or_unproxied_gateway_keeps_default_client(monkeypatch):
    _system_proxy(monkeypatch, set())
    assert _bypass_proxy_mounts("https://api.siliconflow.cn/v1") is None
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    assert _bypass_proxy_mounts("https://ai.example-intranet.cn/v1") is None


def test_openai_client_routes_bypassed_host_directly(monkeypatch):
    _system_proxy(monkeypatch, {"ai.example-intranet.cn"})
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("NO_PROXY", "")
    http = LLM(mode="api", model="m", api_base="https://ai.example-intranet.cn/v1", api_key="sk-test", audit=False)._client._client
    # 网关主机直连（默认连接池）；其他主机仍按系统代理走。
    assert http._transport_for_url(httpx.URL("https://ai.example-intranet.cn/v1/chat/completions")) is http._transport
    assert http._transport_for_url(httpx.URL("https://api.siliconflow.cn/v1/models")) is not http._transport

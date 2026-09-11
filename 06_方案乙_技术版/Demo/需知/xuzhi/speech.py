"""业务口述：浏览器 Web Speech API，识别结果写入原话。无需额外 Python 依赖。"""
from __future__ import annotations

from pathlib import Path

import streamlit.components.v1 as components

_FRONTEND = Path(__file__).resolve().parent / "speech_frontend"
_dictation = components.declare_component("xuzhi_dictation", path=str(_FRONTEND))


def append_dictation(prev: str, chunk: str) -> str:
    """把新口述接在当前框内文字后面。prev 以框里现有内容为准（删空则为空）。"""
    prev = "" if prev is None else str(prev)
    chunk = (chunk or "").strip()
    if not chunk:
        return prev.strip()
    prev = prev.strip()
    if not prev:
        return chunk
    if chunk in prev:
        return prev
    return prev + "\n" + chunk


def dictation_bar():
    """返回 {text, ts, box, has_box} 或 None。box 为原话框当时的文字。"""
    return _dictation(default=None, key="xuzhi_dictation")

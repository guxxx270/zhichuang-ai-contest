"""口述转写：OpenAI 兼容 /v1/audio/transcriptions（默认硅基流动 SenseVoice）。"""
from __future__ import annotations

import io
import re
from typing import Any

DOMAIN_PROMPT = (
    "期货资管业务需求口述。"
    "专有名词：净值日报、对标指数、单位净值、累计净值、回撤、集中度、杠杆、限仓、"
    "保证金、基差、升贴水、结算后、日终、客户版、脱敏、沪深300、中证500、中证1000。"
)

DEFAULT_ASR_BASE = "https://api.siliconflow.cn/v1"
DEFAULT_ASR_MODEL = "FunAudioLLM/SenseVoiceSmall"

# 浏览器 / 通用 ASR 常见听错。长词优先。
_HOMOPHONES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"对[标表兑][指只之][数是示]"), "对标指数"),
    (re.compile(r"对标指[是示]"), "对标指数"),
    (re.compile(r"净值日[包抱]"), "净值日报"),
    (re.compile(r"单位井值"), "单位净值"),
    (re.compile(r"累计井值"), "累计净值"),
    (re.compile(r"沪深三[百0０零]{1,2}"), "沪深300"),
    (re.compile(r"沪深三零零"), "沪深300"),
    (re.compile(r"中证五百"), "中证500"),
    (re.compile(r"中证一千"), "中证1000"),
    (re.compile(r"升贴税"), "升贴水"),
    (re.compile(r"集中读"), "集中度"),
    (re.compile(r"脱[民敏]"), "脱敏"),
    (re.compile(r"客户办"), "客户版"),
    (re.compile(r"限舱"), "限仓"),
    (re.compile(r"企微"), "企业微信"),
    (re.compile(r"HS\s*300"), "沪深300"),
    (re.compile(r"CSI\s*500"), "中证500"),
]


def _sniff_filename(data: bytes, filename: str) -> str:
    name = filename or "speech.wav"
    if data[:4] == b"RIFF":
        return "speech.wav"
    if data[:4] == b"OggS":
        return "speech.ogg"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "speech.webm"
    if data[4:8] == b"ftyp":
        return "speech.m4a"
    if "." in name:
        return name
    return "speech.wav"


def correct_domain_speech(text: str) -> str:
    """去掉汉字间空格，并把常见听错改成领域词。"""
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    for pat, dest in _HOMOPHONES:
        s = pat.sub(dest, s)
    return s.strip()


def transcribe_audio(
    data: bytes,
    filename: str = "speech.wav",
    api_base: str = DEFAULT_ASR_BASE,
    api_key: str = "",
    model: str = DEFAULT_ASR_MODEL,
) -> str:
    if not data:
        raise ValueError("没有录音数据")
    if not api_key.strip():
        raise ValueError("没有语音识别 Key")
    from openai import OpenAI

    base = (api_base or DEFAULT_ASR_BASE).rstrip("/")
    client = OpenAI(base_url=base, api_key=api_key.strip(), timeout=90)
    name = _sniff_filename(data, filename)
    siliconflow = "siliconflow" in base

    def _file() -> io.BytesIO:
        buf = io.BytesIO(data)
        buf.name = name
        return buf

    kwargs: dict[str, Any] = {"model": model or DEFAULT_ASR_MODEL, "file": _file()}
    if not siliconflow:
        kwargs["language"] = "zh"
        kwargs["prompt"] = DOMAIN_PROMPT
    try:
        text = client.audio.transcriptions.create(**kwargs).text
    except Exception:
        if siliconflow:
            raise
        kwargs = {"model": model or DEFAULT_ASR_MODEL, "file": _file()}
        text = client.audio.transcriptions.create(**kwargs).text
    return correct_domain_speech(text or "")

"""配置：从 .env 读取 LLM 接入信息。key 由使用者自己填，代码里不出现。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

LLM_API_BASE = os.getenv("LLM_API_BASE", "").strip()      # 例：https://ai.company.internal/v1
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()            # 例：qwen-plus / deepseek-chat
LLM_MODE = os.getenv("LLM_MODE", "").strip().lower()      # auto / api / mock

DATA_DIR = ROOT / "data"
DB_PATH = ROOT / "data" / "ledger.sqlite3"
PROMPT_DIR = ROOT / "prompts"


def resolved_mode() -> str:
    """没有 key 就自动退到 mock，保证 Demo 任何时候都能跑。"""
    if LLM_MODE in ("api", "mock"):
        return LLM_MODE
    return "api" if (LLM_API_KEY and LLM_API_BASE and LLM_MODEL) else "mock"

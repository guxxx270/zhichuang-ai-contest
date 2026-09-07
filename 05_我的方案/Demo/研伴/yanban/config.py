"""配置：从 .env 读取 LLM 接入信息（key 由使用者自填，代码不含密钥）；各数据目录。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env", override=False)

LLM_API_BASE = os.getenv("LLM_API_BASE", "").strip()   # OpenAI 兼容网关，例：https://ai.company.internal/v1
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
LLM_MODE = os.getenv("LLM_MODE", "").strip().lower()   # auto / api / mock

DATA_DIR = ROOT / "data"
DOCS_DIR = DATA_DIR / "sample_docs"
RULES_DIR = DATA_DIR / "rules"
TEMPLATES_DIR = DATA_DIR / "templates"
PROMPT_DIR = ROOT / "prompts"
PROFILE_PATH = DATA_DIR / "profile.json"
MEMORY_DB = DATA_DIR / "memory.sqlite3"
EVENTS_CSV = DATA_DIR / "events.csv"
COMPLIANCE_TERMS = DATA_DIR / "compliance_terms.json"

PRODUCT_NAME = "研伴"
PRODUCT_SLOGAN = "把研究员的一天，还给研究。"
BRAND = {"blue": "#0B3D91", "gold": "#E0A100", "paper": "#F7F5F0", "ink": "#1F2937", "mist": "#E5E7EB"}


def resolved_mode() -> str:
    """没有 key 就自动退到 mock，保证 Demo 任何时候都能跑。"""
    if LLM_MODE in ("api", "mock"):
        return LLM_MODE
    return "api" if (LLM_API_KEY and LLM_API_BASE and LLM_MODEL) else "mock"

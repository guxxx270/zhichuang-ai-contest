"""配置：从 .env 读取 LLM 接入信息（key 由使用者自填，代码不含密钥）；各数据目录；品牌常量。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:      # 没装 python-dotenv 也能跑（环境变量或 mock）
    pass

LLM_API_BASE = os.getenv("LLM_API_BASE", "").strip()   # OpenAI 兼容网关，例：https://ai.company.internal/v1
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
LLM_MODE = os.getenv("LLM_MODE", "").strip().lower()   # auto / api / mock

DATA_DIR = ROOT / "data"
SAMPLES_DIR = DATA_DIR / "samples"
HISTORY_PATH = DATA_DIR / "history_requirements.json"
KNOWLEDGE_DIR = ROOT / "knowledge"
PROBES_PATH = KNOWLEDGE_DIR / "futures_probes.json"
CATALOG_PATH = KNOWLEDGE_DIR / "system_catalog.json"
PROMPT_DIR = ROOT / "prompts"
LEDGER_DB = DATA_DIR / "ledger.sqlite3"

PRODUCT_NAME = "需知"
PRODUCT_EN = "XuZhi"
PRODUCT_SLOGAN = "一堆话进来，一份能开工的需求出去。"
SKILLS = [("问清", "先问清，再开工"), ("写单", "业务版 · 技术版"), ("估量", "不拍脑袋的工时"),
          ("定架", "告诉领导要拍板什么"), ("出样", "看得见的需求"), ("对账", "改了什么一眼看 · 二期")]
BRAND = {"navy": "#14367A", "accent": "#E07A1F", "paper": "#F6F4EF", "ink": "#1F2937", "mist": "#E5E7EB", "teal": "#0F766E"}

# 估量参数（可按公司实际校准）
DAYS_PER_COMPLEXITY_POINT = 1.6      # 复杂度总分 → 基础人天 的系数
ESTIMATE_BLEND_HISTORY = 0.6         # 有相似历史需求时，历史实际人天的权重


def resolved_mode() -> str:
    """没有 key 就自动退到 mock，保证 Demo 任何时候都能跑。"""
    if LLM_MODE in ("api", "mock"):
        return LLM_MODE
    return "api" if (LLM_API_KEY and LLM_API_BASE and LLM_MODEL) else "mock"

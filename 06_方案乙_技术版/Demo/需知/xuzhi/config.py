"""配置：公开的模型/网关预设（不含密钥）；各数据目录；品牌常量。

API Key 不在代码与仓库中：页面上按模型填写，仅存于当前浏览器会话。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
except ImportError:
    pass

# 可选：仅作本地调试兜底；正式用法是页面输入。仓库默认留空。
LLM_API_BASE = os.getenv("LLM_API_BASE", "").strip()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
LLM_MODE = os.getenv("LLM_MODE", "").strip().lower()   # auto / api / mock

# 页面预设：(显示名, mode, provider_id, api_base, model_id)
# model_id 为 "__custom__" 时需在页面填写模型名；provider=custom 时还需填网关。
LLM_PRESETS: list[tuple[str, str, str, str, str]] = [
    ("mock · 规则引擎（不调 API）", "mock", "mock", "", ""),
    ("硅基流动 · DeepSeek-V4-Flash", "api", "siliconflow", "https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V4-Flash"),
    ("硅基流动 · DeepSeek-V3", "api", "siliconflow", "https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V3"),
    ("硅基流动 · Qwen2.5-72B", "api", "siliconflow", "https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-72B-Instruct"),
    ("Qoder Cloud Agents · 国内", "api", "qoder-cloud", "https://api.qoder.com.cn", "ultimate"),
    ("Qoder Cloud Agents · 国际", "api", "qoder-cloud-intl", "https://api.qoder.com", "ultimate"),
    ("自定义 OpenAI 兼容网关", "api", "custom", "", "__custom__"),
]

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

DAYS_PER_COMPLEXITY_POINT = 1.6
ESTIMATE_BLEND_HISTORY = 0.6


def resolved_mode() -> str:
    """没有 key 就自动退到 mock，保证 Demo 任何时候都能跑。"""
    if LLM_MODE in ("api", "mock"):
        return LLM_MODE
    return "api" if (LLM_API_KEY and LLM_API_BASE and LLM_MODEL) else "mock"

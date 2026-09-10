"""测试强制 mock，避免误调外部模型。"""
from __future__ import annotations

import os

os.environ["LLM_MODE"] = "mock"
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_API_BASE"] = ""
os.environ["LLM_MODEL"] = ""

from xuzhi import config

config.LLM_MODE = "mock"
config.LLM_API_KEY = ""
config.LLM_API_BASE = ""
config.LLM_MODEL = ""

"""测试强制 mock，避免误调外部模型。"""
from __future__ import annotations

import os
import tempfile

os.environ["LLM_MODE"] = "mock"
# 测试期间一本账 / 记忆 / 审计写到临时库，绝不碰 data/ledger.sqlite3
os.environ["XUZHI_LEDGER_DB"] = os.path.join(tempfile.mkdtemp(prefix="xuzhi_test_"), "ledger.sqlite3")
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_API_BASE"] = ""
os.environ["LLM_MODEL"] = ""

from xuzhi import config

config.LLM_MODE = "mock"
config.LLM_API_KEY = ""
config.LLM_API_BASE = ""
config.LLM_MODEL = ""
config.LEDGER_DB = type(config.LEDGER_DB)(os.environ["XUZHI_LEDGER_DB"])
config.HISTORY_LEARNED_PATH = config.LEDGER_DB.with_name("history_learned.json")   # 对账回写也进临时目录

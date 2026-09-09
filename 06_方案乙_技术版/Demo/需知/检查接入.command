#!/bin/bash
# 双击：检查 .env 里的 AI 平台接入是否可用，并用样例 1 跑一遍 api 模式（Mac）。
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then echo "首次运行，安装依赖……"; python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt; fi
./.venv/bin/python - <<'PY'
import time
from xuzhi import config
from xuzhi.llm import LLM
print(f"模式：{config.resolved_mode()}｜网关：{config.LLM_API_BASE or '(空)'}｜模型：{config.LLM_MODEL or '(空)'}")
llm = LLM()
if llm.mode != "api":
    print("未进入 api 模式：请检查 .env 的 LLM_API_BASE / LLM_API_KEY / LLM_MODEL 是否都填了。"); raise SystemExit(1)
t0 = time.time()
try:
    out = llm.chat_json("只输出 JSON。", '请返回 {"ok": true, "model": "你的模型名"}')
    print(f"连通 ✅ 用时 {time.time()-t0:.1f}s，返回：{out}")
except Exception as e:
    print(f"连通 ❌ {e}"); raise SystemExit(1)
PY
[ $? -eq 0 ] && ./.venv/bin/python run_demo.py 1
echo; echo "（按回车关闭）"; read -r

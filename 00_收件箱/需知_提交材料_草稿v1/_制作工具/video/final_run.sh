#!/bin/bash
# 正式录制：从主目录启动需知（读它自己的 .env，模型由 .env 配置），一本账用临时库，不碰正式数据。
#   bash final_run.sh [--model]      # --model：页面已由 .env 配好模型（自定义网关自动带出）
set -e
SP="$(cd "$(dirname "$0")/.." && pwd)"   # _制作工具 根目录
APP="${XUZHI_APP:-$SP/../../../06_方案乙_技术版/Demo/需知}"   # 拷到别处运行时用 XUZHI_APP 指向需知目录
APP="$(cd "$APP" && pwd)"
PY="$APP/.venv/bin/python"
TOOLS="${TOOLS_PY:-$SP/.venv/bin/python}"   # 装了 playwright / imageio-ffmpeg / pillow 的 Python
RUN="${RUN_DIR:-$SP/work}"
PORT="${PORT:-8710}"
MODEL_FLAG="${1:-}"
mkdir -p "$RUN"
export XUZHI_LEDGER_DB="$RUN/ledger.sqlite3"
export LLM_TIMEOUT="${LLM_TIMEOUT:-240}"   # 录制环境放宽单次模型调用上限（产品默认 90 秒），免得长估时超时
export XUZHI_HISTORY_LEARNED="$RUN/history_learned.json"   # 新版跟随一本账目录；旧版忽略此变量也无害

seed() {   # 演示数据：在临时副本里跑 demo_seed（备份写进副本，不写主目录）
  rm -rf "$RUN/seedcopy" && mkdir -p "$RUN/seedcopy"
  rsync -a --exclude .venv --exclude .env --exclude '.env.bak*' --exclude __pycache__ --exclude .pytest_cache "$APP/" "$RUN/seedcopy/"
  (cd "$RUN/seedcopy" && env LLM_MODE=mock LLM_API_KEY= LLM_API_BASE= LLM_MODEL= "$PY" tools/demo_seed.py | tail -2)
}

start() {
  pkill -f "server.port $PORT" 2>/dev/null || true
  sleep 1
  if [ "$MODEL_FLAG" = "--model" ]; then
    (cd "$APP" && nohup "$PY" -m streamlit run app.py --server.port $PORT --server.headless true --browser.gatherUsageStats false > "$RUN/streamlit.log" 2>&1 &)
  else
    (cd "$APP" && LLM_MODE=mock LLM_API_KEY= LLM_API_BASE= LLM_MODEL= nohup "$PY" -m streamlit run app.py --server.port $PORT --server.headless true --browser.gatherUsageStats false > "$RUN/streamlit.log" 2>&1 &)
  fi
  for i in $(seq 1 40); do curl -s -o /dev/null http://127.0.0.1:$PORT/ && break; sleep 0.5; done
}

stop() { pkill -f "server.port $PORT" 2>/dev/null || true; }
trap stop EXIT

case "${2:-all}" in
  capture|all)
    seed; start
    (cd "$SP/video" && "$TOOLS" capture.py http://127.0.0.1:$PORT/ "$SP/shots" $MODEL_FLAG)
    stop ;;
esac
case "${2:-all}" in
  record|all)
    seed; start
    (cd "$SP/video" && "$TOOLS" record_app.py http://127.0.0.1:$PORT/ $MODEL_FLAG)
    stop ;;
esac
echo "done"

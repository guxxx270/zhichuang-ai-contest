#!/usr/bin/env bash
# 在仓库根目录 git pull 后运行；模型配置和业务数据均保留，网页免登录。
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ ${EUID:-$(id -u)} == 0 ]] || { echo '请用 root 或 sudo 运行本脚本。' >&2; exit 1; }
for program in docker flock curl git tar; do command -v "$program" >/dev/null || { echo "缺少 $program" >&2; exit 1; }; done
docker compose version >/dev/null
umask 077
mkdir -p .deploy/data
exec 9>.deploy/update.lock
flock -n 9 || { echo '已有更新正在执行。' >&2; exit 1; }
# Docker 24 的 gRPC 元数据不接受中文 COPY 路径。仅从 Git 提取已提交源码到英文构建目录。
# .env 和运行数据从不参与构建，.dockerignore 再做一层过滤。
XUZHI_BUILD_CONTEXT="$(mktemp -d "$ROOT/.deploy/build.XXXXXX")"
export XUZHI_BUILD_CONTEXT
trap 'rm -rf -- "$XUZHI_BUILD_CONTEXT"' EXIT
mkdir "$XUZHI_BUILD_CONTEXT/app"
git archive HEAD -- '06_方案乙_技术版/Demo/需知' | tar -xf - --strip-components=3 -C "$XUZHI_BUILD_CONTEXT/app"
cp deploy/Dockerfile "$XUZHI_BUILD_CONTEXT/Dockerfile"
cp .dockerignore "$XUZHI_BUILD_CONTEXT/.dockerignore"
ENV_FILE="$ROOT/06_方案乙_技术版/Demo/需知/.env"
[[ -f "$ENV_FILE" ]] || { echo "缺少 $ENV_FILE，请先写入模型配置。" >&2; exit 1; }
chmod 600 "$ENV_FILE"
if [[ ! -f .deploy/compose.env ]]; then
    printf 'XUZHI_PORT=5030\n' > .deploy/compose.env
fi
chmod 600 .deploy/compose.env
chmod 644 deploy/nginx.conf
compose() { docker compose --env-file .deploy/compose.env "$@"; }
compose config --quiet
chown 10001:10001 .deploy/data
chmod 700 .deploy/data

# 备份使用 SQLite 在线备份接口，不复制正在写入的数据库文件。
if [[ -n "$(compose ps --status running -q app)" ]]; then
    compose exec -T app python - <<'PY'
from pathlib import Path
import datetime, shutil, sqlite3
root = Path('/var/lib/xuzhi')
db = root / 'ledger.sqlite3'
if db.exists():
    dest = root / 'backups' / datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    dest.mkdir(parents=True)
    with sqlite3.connect(str(db)) as source, sqlite3.connect(str(dest / db.name)) as target:
        source.backup(target)
    history = root / 'history_learned.json'
    if history.exists():
        shutil.copy2(history, dest / history.name)
    print('已备份一本账和历史记录。')
PY
fi

PREVIOUS_IMAGE="$(docker image inspect zhichuang-xuzhi:local --format '{{.Id}}' 2>/dev/null || true)"
echo '构建需知镜像（依赖未变时会复用缓存）……'
compose build app </dev/null
compose run --rm --no-deps --entrypoint nginx web -t </dev/null
echo '更新服务并等待健康检查……'
if ! compose up -d --no-build --wait --wait-timeout 180 </dev/null; then
    if [[ -n "$PREVIOUS_IMAGE" ]]; then
        docker tag "$PREVIOUS_IMAGE" zhichuang-xuzhi:local
        compose up -d --no-build </dev/null
        echo '应用启动失败，已恢复上一镜像；请查看 docker compose logs。' >&2
    fi
    exit 1
fi
PORT="$(sed -n 's/^XUZHI_PORT=//p' .deploy/compose.env | tail -1)"
PORT="${PORT:-5030}"
# CentOS 7 的 curl 不支持 --retry-connrefused，用 shell 重试兼容旧版本。
PROXY_READY=0
for attempt in {1..6}; do
    if curl --noproxy '*' --fail --silent --show-error --connect-timeout 3 --max-time 5 "http://127.0.0.1:$PORT/healthz" >/dev/null; then
        PROXY_READY=1
        break
    fi
    sleep 1
done
[[ "$PROXY_READY" == 1 ]] || { echo '网页入口健康检查失败。' >&2; exit 1; }
compose ps
printf '\n部署完成，网页端口 %s；打开即可使用，无需登录。\n' "$PORT"
printf '以后更新：cd %q && git pull --ff-only && ./scripts/deploy.sh\n' "$ROOT"

#!/usr/bin/env bash
# 在仓库根目录 git pull 后运行；模型配置、访问口令和业务数据均保留。
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[[ ${EUID:-$(id -u)} == 0 ]] || { echo '请用 root 或 sudo 运行本脚本。' >&2; exit 1; }
for program in docker openssl flock curl; do command -v "$program" >/dev/null || { echo "缺少 $program" >&2; exit 1; }; done
docker compose version >/dev/null
umask 077
mkdir -p .deploy/auth .deploy/data
exec 9>.deploy/update.lock
flock -n 9 || { echo '已有更新正在执行。' >&2; exit 1; }
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
if [[ ! -f .deploy/auth/htpasswd ]]; then
    ACCESS_PASSWORD="$(openssl rand -hex 12)"
    ACCESS_HASH="$(printf '%s\n' "$ACCESS_PASSWORD" | openssl passwd -apr1 -stdin)"
    printf 'xuzhi:%s\n' "$ACCESS_HASH" > .deploy/auth/htpasswd
    printf 'username=xuzhi\npassword=%s\n' "$ACCESS_PASSWORD" > .deploy/access.txt
    unset ACCESS_PASSWORD ACCESS_HASH
fi
chown root:10001 .deploy/auth/htpasswd
chmod 640 .deploy/auth/htpasswd
chmod 600 .deploy/access.txt

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
curl --noproxy '*' --fail --silent --show-error --retry 5 --retry-connrefused --retry-delay 1 "http://127.0.0.1:$PORT/healthz" >/dev/null
compose ps
printf '\n部署完成，网页端口 %s；访问口令保存在 %s/.deploy/access.txt\n' "$PORT" "$ROOT"
printf '以后更新：cd %q && git pull --ff-only && ./scripts/deploy.sh\n' "$ROOT"

# 服务器部署（需知）

服务器上更新只需：

```bash
cd /opt/zhichuang-ai-contest
git pull --ff-only
./scripts/deploy.sh
```

入口默认 `http://服务器IP:5030/`，包含零散需求、Jira 评估与内置隐盾。Web 入口使用独立访问口令；首次部署生成的账号密码在 `.deploy/access.txt`，只有 root 可读。Streamlit 只在 Docker 内网开放，前面的独立 Nginx 容器处理认证与 WebSocket，不修改服务器原有 Nginx。

首次部署需要 Docker Engine、Docker Compose v2、Git、OpenSSL、curl、flock。为服务器配置此 GitHub 私有仓库的只读部署密钥，再克隆到 `/opt/zhichuang-ai-contest`。在 `06_方案乙_技术版/Demo/需知/.env` 中保存模型配置（权限 600）；脚本不会覆盖或打印它，Docker 构建也会排除它。Compose 在启动时读取 `.env`，修改后重新执行脚本即可生效。

`.deploy/compose.env` 可指定 `XUZHI_PORT`（默认 5030）和 `XUZHI_NGINX_IMAGE`（默认 `nginx:1.28-alpine`）。服务器有可用的内部镜像时可指定其镜像名或 digest。构建需要访问 Debian 软件源和 PyPI，基础镜像固定使用 Python 3.12 的 uv 镜像。脚本从当前 Git 提交提取源码到英文临时目录构建，兼容旧 Docker 对中文目录的限制；未提交文件不会进入镜像，临时目录在结束时删除。

一本账、追问记忆、审计与历史对账数据持久保存在 `.deploy/data/`。每次更新前对现有 SQLite 做在线备份，放在 `.deploy/data/backups/`；更新不会重置演示数据。构建失败时旧容器继续运行；新容器健康检查失败时脚本尝试恢复上一应用镜像。源码、数据库结构或 Nginx 配置不兼容时，仍需检查日志并人工回退相应提交。

本部署只启动 Web；`.env` 中企微和问码的配置会保留，企微机器人长连接需另行启动，同一机器人不要同时运行两份。

常用检查（在仓库根目录执行）：

```bash
docker compose --env-file .deploy/compose.env ps
docker compose --env-file .deploy/compose.env logs --tail=100 app web
curl http://127.0.0.1:5030/healthz
cat .deploy/access.txt
```

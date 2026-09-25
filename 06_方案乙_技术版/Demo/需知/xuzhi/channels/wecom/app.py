"""需知 · 企微入口的启动入口：python -m xuzhi.channels.wecom.app

前台常驻，Ctrl+C 退出。同一个机器人同一时间只能有一条长连接，别和别的脚本同时跑。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from ... import config as xuzhi_config
from ... import reminders
from .config import load_wecom_config
from .connection import WeComLongConnection
from .handler import WecomHandler

log = logging.getLogger("xuzhi.wecom")

MISSING_ENV = """\
缺少企微机器人凭据。请在 需知 Demo 目录的 .env 里加上（该文件已 gitignore）：

    WECOM_BOT_ID=企微后台「智能机器人 → API 模式 → 使用长连接」里的 Bot ID
    WECOM_BOT_SECRET=同一页面的 Secret
    WECOM_ALLOW_USERS=你的企微 userid      # 先随便填一个，启动后在企微发 /whoami 看真实值再改
    # WECOM_AUTH_MODE=allow_all            # 本机调试想跳过白名单时才打开

然后重新运行。"""


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)


def _llm_banner() -> str:
    mode = xuzhi_config.resolved_mode()
    if mode == "api":
        return f"模型：api · {xuzhi_config.LLM_MODEL or '(未指定模型名)'}"
    return "模型：mock（.env 未配 LLM_API_BASE/LLM_API_KEY/LLM_MODEL）——流水线仍可跑，但结论由规则引擎给出"


async def main_async(verbose: bool) -> int:
    cfg = load_wecom_config()
    if not cfg.configured:
        print(MISSING_ENV)
        return 2

    holder: dict = {}

    async def on_message(m):
        await holder["handler"].handle(m)

    conn = WeComLongConnection(cfg.bot, on_message)
    holder["handler"] = WecomHandler(cfg, conn)

    log.info("%s · 企微入口启动 | 机器人「%s」 | 白名单 %s(%d 人) | %s",
             xuzhi_config.PRODUCT_NAME, cfg.bot.name, cfg.auth.mode, len(cfg.auth.users), _llm_banner())
    log.info("提示：同一机器人只允许一条长连接；启动后在企微里 @机器人 发一段需求即可")

    digest_task = _start_digest_scheduler(holder["handler"])

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(conn.stop()))
        except NotImplementedError:      # Windows
            pass
    try:
        await conn.run_forever()
    finally:
        if digest_task:
            digest_task.cancel()
    return 0


def _start_digest_scheduler(handler) -> "asyncio.Task | None":
    """附加功能：每日定时推送催办摘要。默认关闭——要同时满足三件事才会跑：
    .env 里 XUZHI_DIGEST_TIME=HH:MM、XUZHI_DIGEST_WEBHOOK=企微群机器人地址、sandbox.yaml notify.enabled=true。"""
    hhmm = (os.getenv("XUZHI_DIGEST_TIME", "") or "").strip()
    if not hhmm:
        log.info("催办摘要定时推送：未配置 XUZHI_DIGEST_TIME，不启用（聊天里可随时发 /摘要 拉取）")
        return None
    ok, why = reminders.push_enabled()
    if not ok:
        log.info("催办摘要定时推送：已配时间但不推送 —— %s", why)
        return None

    async def loop_forever() -> None:
        while True:
            wait = reminders.seconds_until(hhmm)
            log.info("催办摘要：下次 %s 推送（%d 分钟后）", hhmm, int(wait // 60))
            await asyncio.sleep(wait)
            try:
                sent, msg = await asyncio.to_thread(reminders.send_digest, handler.ledger)
                log.info("催办摘要：%s%s", "✅ " if sent else "⏸ ", msg)
            except Exception:   # noqa: BLE001
                log.exception("催办摘要推送失败")
            await asyncio.sleep(61)   # 避免同一分钟重复触发

    log.info("催办摘要定时推送：已启用，每日 %s（只发数量、标题、天数）", hhmm)
    return asyncio.create_task(loop_forever())


def main() -> None:
    ap = argparse.ArgumentParser(description="需知 · 企业微信入口")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    setup_logging(args.verbose)
    try:
        sys.exit(asyncio.run(main_async(args.verbose)))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

"""企微入口：一本账记账 + 追问记忆 + /摘要 /记忆 命令。复用 test_wecom_channel 的模拟网关，但注入真实流水线与临时库。"""
from __future__ import annotations

import asyncio

from xuzhi.channels.wecom.connection import WeComLongConnection
from xuzhi.channels.wecom.handler import WecomHandler
from xuzhi.ledger import Ledger
from xuzhi.memory import Memory
from xuzhi.pipeline import analyze

from test_wecom_channel import MockOpenWS, make_cfg

REQ = "资管运营小周：净值日报能不能加一列'较上一交易日变动'，风险指标也放进去，周五给客户那版也要，最好下周就能用。"
REQ2 = "资管运营：净值日报再加一列累计净值，也要给客户版，下周要。"


class Rig:
    def __init__(self, tmp_path) -> None:
        self.ledger = Ledger(tmp_path / "l.sqlite3")
        self.memory = Memory(tmp_path / "l.sqlite3")

    async def __aenter__(self):
        self.mock = MockOpenWS()
        await self.mock.start()
        cfg = make_cfg(self.mock.port, False)
        holder: dict = {}

        async def on_message(m):
            await holder["h"].handle(m)

        self.conn = WeComLongConnection(cfg.bot, on_message)
        holder["h"] = WecomHandler(cfg, self.conn, analyze_fn=lambda *a, **k: analyze(*a, llm_polish=False, **k),
                                   ledger=self.ledger, memory=self.memory)
        self.handler = holder["h"]
        self.task = asyncio.create_task(self.conn.run_forever())
        await asyncio.wait_for(self.mock.subscribed.wait(), 5)
        return self

    async def __aexit__(self, *exc):
        await self.conn.stop()
        self.task.cancel()
        await self.mock.stop()
        return False


def test_wecom_books_ledger_and_learns_memory(tmp_path):
    async def main():
        async with Rig(tmp_path) as rig:
            mock = rig.mock
            await mock.push("w1", "025058", f"@需知 {REQ}")
            final = await mock.wait_final("w1")
            assert "先和业务确认" in final
            rows = rig.ledger.recent()
            assert len(rows) == 1 and rows[0]["source"] == "企业微信" and rows[0]["status"] == "受理"
            rid = rows[0]["id"]
            assert any(e["action"] == "企微受理" for e in rig.ledger.events_of(rid))
            # 按编号答两题 → 同一条账更新，口径进记忆
            await mock.push("w2", "025058", "1 以上一交易日结算价为基准\n2 客户版隐去内部字段")
            final2 = await mock.wait_final("w2")
            assert "已收到 2 条答复" in final2
            assert len(rig.ledger.recent()) == 1
            row = rig.ledger.get(rid)
            assert row["n_answered"] >= 2 and row["status"] == "已确认"
            assert rig.memory.stats()["entries"] >= 1
            # 换一条同提出方的需求 → 沿用，不再问
            await mock.push("w3", "025058", "/reset")
            await mock.wait_final("w3")
            await mock.push("w4", "025058", f"@需知 {REQ2}")
            final4 = await mock.wait_final("w4")
            assert "沿用上次口径" in final4
            assert len(rig.ledger.recent()) == 2
            # 命令：/摘要 与 /记忆
            await mock.push("w5", "025058", "/摘要")
            out = await mock.wait_final("w5")
            assert "催办摘要" in out and ("本周受理" in out or "暂无" in out)
            await mock.push("w6", "025058", "/记忆")
            out = await mock.wait_final("w6")
            assert "追问记忆" in out and "累计沿用" in out
            await mock.push("w7", "025058", "/记忆 清 资管运营")
            out = await mock.wait_final("w7")
            assert "已清掉" in out
            await mock.push("w8", "025058", "/记忆 清空")
            assert "已清空" in await mock.wait_final("w8")
            assert rig.memory.stats()["entries"] == 0
            await mock.push("w9", "025058", "/help")
            assert "/摘要" in await mock.wait_final("w9")

    asyncio.run(main())

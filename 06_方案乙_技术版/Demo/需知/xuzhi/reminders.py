"""催办摘要（附加功能，默认不推送）：从一本账里算出"该动一动"的需求。

三块内容，全部只有数量、需求标题、等待天数——不含原话、不含客户信息：
1. 待业务确认超期：状态"受理"、还有高影响问题没答、受理超过 N 天；
2. 已交付未对账：状态"已交付"超过 M 天还没回写实际人天（估算飞轮断了）；
3. 本周受理汇总：各部门本周提了几条、各状态几条。

三种用法：
- 拉：企微里发 /摘要，或 Web 一本账页"催办摘要"，或 GET /api/v1/digest；
- 命令行：python -m xuzhi.reminders --print；--send 推到企微群机器人 webhook（配 XUZHI_DIGEST_WEBHOOK）；
- 定时：企微入口进程里可选每日定时（XUZHI_DIGEST_TIME=09:00），或交给系统 cron / launchd 跑上面的命令。
推送必须同时满足 sandbox.yaml notify.enabled=true 且配置了 webhook，缺一不发（push_enabled()）。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from . import config, sandbox


@dataclass
class Item:
    req_id: int
    title: str
    dept: str
    days: int
    detail: str = ""


@dataclass
class Digest:
    as_of: str
    stale_confirm: list[Item] = field(default_factory=list)
    stale_reconcile: list[Item] = field(default_factory=list)
    week_by_dept: dict[str, int] = field(default_factory=dict)
    week_total: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    stale_confirm_days: int = 3
    stale_reconcile_days: int = 5

    @property
    def empty(self) -> bool:
        return not (self.stale_confirm or self.stale_reconcile or self.week_total)


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat((ts or "")[:19])
    except ValueError:
        return None


def build_digest(ledger, now: datetime | None = None) -> Digest:
    """纯读一本账，不写。"""
    now = now or datetime.now()
    d = Digest(as_of=now.strftime("%Y-%m-%d %H:%M"),
               stale_confirm_days=int(sandbox.get("notify.stale_confirm_days", 3) or 3),
               stale_reconcile_days=int(sandbox.get("notify.stale_reconcile_days", 5) or 5))
    rows = ledger.export_rows()
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    for r in rows:
        ts = _parse_ts(r.get("ts") or "")
        status = (r.get("status") or "受理").strip()
        title = (r.get("title") or "未命名需求")[:40]
        dept = (r.get("dept") or "未注明").strip() or "未注明"
        d.by_status[status] = d.by_status.get(status, 0) + 1
        if ts and ts >= week_start:
            d.week_total += 1
            d.week_by_dept[dept] = d.week_by_dept.get(dept, 0) + 1
        if not ts:
            continue
        age = (now - ts).days
        high_open = int(r.get("n_high_open") or 0)
        n_ans = int(r.get("n_answered") or 0)
        if status == "受理" and (high_open > 0 or n_ans == 0) and age >= d.stale_confirm_days:
            d.stale_confirm.append(Item(int(r["id"]), title, dept, age, f"{high_open} 个高影响问题未答" if high_open else "尚无业务答复"))
        if status == "已交付" and age >= d.stale_reconcile_days and not r.get("actual_days"):
            d.stale_reconcile.append(Item(int(r["id"]), title, dept, age, "未回写实际人天"))
    d.stale_confirm.sort(key=lambda i: -i.days)
    d.stale_reconcile.sort(key=lambda i: -i.days)
    return d


def render_digest(d: Digest, limit: int = 8) -> str:
    """企微 / Web 通用 Markdown。只有数量、标题、天数。"""
    lines = [f"**{config.PRODUCT_NAME} · 需求催办摘要**（{d.as_of}）"]
    if d.empty:
        lines.append("一本账里暂无需要跟进的需求。")
        return "\n".join(lines)
    if d.stale_confirm:
        lines.append(f"\n⏳ **待业务确认超 {d.stale_confirm_days} 天：{len(d.stale_confirm)} 条**")
        for it in d.stale_confirm[:limit]:
            lines.append(f"- #{it.req_id} {it.title}（{it.dept}，等了 {it.days} 天，{it.detail}）")
        if len(d.stale_confirm) > limit:
            lines.append(f"- …另有 {len(d.stale_confirm) - limit} 条")
    if d.stale_reconcile:
        lines.append(f"\n📒 **已交付未对账超 {d.stale_reconcile_days} 天：{len(d.stale_reconcile)} 条**（回写实际人天，估算才会越用越准）")
        for it in d.stale_reconcile[:limit]:
            lines.append(f"- #{it.req_id} {it.title}（{it.dept}，交付后 {it.days} 天）")
    if d.week_total:
        depts = "、".join(f"{k} {v}" for k, v in sorted(d.week_by_dept.items(), key=lambda kv: -kv[1])[:6])
        lines.append(f"\n📈 **本周受理 {d.week_total} 条**：{depts}")
    if d.by_status:
        lines.append("状态：" + "，".join(f"{k} {v}" for k, v in d.by_status.items()))
    lines.append("\n_只含数量、标题与天数；详情在需知 Web 端一本账。_")
    return "\n".join(lines)


def digest_to_dict(d: Digest) -> dict[str, Any]:
    return {
        "as_of": d.as_of,
        "stale_confirm_days": d.stale_confirm_days,
        "stale_reconcile_days": d.stale_reconcile_days,
        "stale_confirm": [it.__dict__ for it in d.stale_confirm],
        "stale_reconcile": [it.__dict__ for it in d.stale_reconcile],
        "week_total": d.week_total,
        "week_by_dept": dict(d.week_by_dept),
        "by_status": dict(d.by_status),
        "markdown": render_digest(d),
    }


# ---------------- 推送（默认关闭）----------------
def webhook_url() -> str:
    return os.getenv("XUZHI_DIGEST_WEBHOOK", "").strip()


def push_enabled() -> tuple[bool, str]:
    """两把锁都开才推：sandbox.yaml notify.enabled 与 .env XUZHI_DIGEST_WEBHOOK。"""
    if not sandbox.get("notify.enabled", False):
        return False, "sandbox.yaml notify.enabled=false（默认关闭）"
    if not webhook_url():
        return False, "未配置 XUZHI_DIGEST_WEBHOOK（企微群机器人 webhook）"
    return True, ""


def push_webhook(markdown: str, url: str = "", timeout: int = 10) -> dict[str, Any]:
    """企微「群机器人」webhook（后台 → 群 → 添加群机器人 → 复制 webhook 地址）。内容按 markdown 类型发。"""
    from urllib.request import Request, urlopen

    url = url or webhook_url()
    if not url.startswith("https://"):
        raise ValueError("webhook 地址必须是 https://")
    body = json.dumps({"msgtype": "markdown", "markdown": {"content": markdown[:4000]}}, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace") or "{}")


def send_digest(ledger, force: bool = False) -> tuple[bool, str]:
    """算摘要并推送。force=True 跳过 notify.enabled 检查（仅命令行 --send --force 用于实测），仍需 webhook。"""
    ok, why = push_enabled()
    if not ok and not (force and webhook_url()):
        return False, why
    d = build_digest(ledger)
    if d.empty:
        return False, "暂无需要跟进的需求，不推送"
    res = push_webhook(render_digest(d))
    if int(res.get("errcode", 0) or 0) != 0:
        return False, f"企微返回 {res}"
    return True, f"已推送：待确认 {len(d.stale_confirm)} 条、待对账 {len(d.stale_reconcile)} 条、本周 {d.week_total} 条"


def seconds_until(hhmm: str, now: datetime | None = None) -> float:
    """到下一次 HH:MM 还有多少秒（今天已过则算明天）。"""
    now = now or datetime.now()
    h, m = [int(x) for x in hhmm.strip().split(":")[:2]]
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .ledger import Ledger

    ap = argparse.ArgumentParser(description=f"{config.PRODUCT_NAME} · 需求催办摘要（附加功能）")
    ap.add_argument("--print", action="store_true", help="打印摘要（默认）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--send", action="store_true", help="推送到企微群机器人 webhook（需 notify.enabled 且配 XUZHI_DIGEST_WEBHOOK）")
    ap.add_argument("--force", action="store_true", help="与 --send 连用：跳过 notify.enabled 检查做一次实测")
    args = ap.parse_args(argv)
    ledger = Ledger()
    if args.send:
        ok, msg = send_digest(ledger, force=args.force)
        print(("✅ " if ok else "⏸ ") + msg)
        return 0 if ok else 1
    d = build_digest(ledger)
    print(json.dumps(digest_to_dict(d), ensure_ascii=False, indent=2) if args.json else render_digest(d))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:   # noqa: BLE001
            pass
    sys.exit(main())

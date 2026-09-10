"""出样：把需求卡片变成一张可点的 HTML 原型页（假数据、公司 VI 风格），让业务"看得见需求"。
也提供 layers_html：定架用的分层架构图。全部为自包含 HTML，不依赖外网。"""
from __future__ import annotations

import html
import random

from .. import config, textutil
from .intake import Card

B = config.BRAND
CSS = f"""
<style>
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; background:{B['paper']}; color:{B['ink']}; }}
.xz-top {{ background:linear-gradient(135deg,{B['navy']},#1f4aa8); color:#fff; padding:14px 18px; display:flex; align-items:center; justify-content:space-between; }}
.xz-top h2 {{ margin:0; font-size:18px; letter-spacing:1px; }}
.xz-top .who {{ font-size:12px; opacity:.85; }}
.xz-wrap {{ padding:14px 18px; }}
.xz-bar {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:12px; }}
.xz-bar select, .xz-bar input {{ border:1px solid {B['mist']}; border-radius:8px; padding:6px 10px; background:#fff; font-size:13px; }}
.xz-btn {{ background:{B['navy']}; color:#fff; border:none; border-radius:8px; padding:7px 14px; font-size:13px; cursor:pointer; }}
.xz-btn.ghost {{ background:#fff; color:{B['navy']}; border:1px solid {B['navy']}; }}
.xz-btn.warn {{ background:{B['accent']}; }}
.xz-kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; margin-bottom:12px; }}
.xz-kpi {{ background:#fff; border:1px solid {B['mist']}; border-left:5px solid {B['accent']}; border-radius:10px; padding:10px 12px; }}
.xz-kpi .n {{ font-size:22px; font-weight:700; color:{B['navy']}; }}
.xz-kpi .l {{ font-size:12px; color:#6b7280; }}
table.xz {{ width:100%; border-collapse:collapse; background:#fff; border:1px solid {B['mist']}; border-radius:10px; overflow:hidden; font-size:13px; }}
table.xz th {{ background:#eef3fb; color:{B['navy']}; text-align:left; padding:8px 10px; font-weight:600; }}
table.xz td {{ padding:8px 10px; border-top:1px solid {B['mist']}; }}
.up {{ color:#b91c1c; }} .down {{ color:#047857; }}
.tag {{ display:inline-block; border-radius:999px; padding:1px 8px; font-size:11px; background:#eef3fb; color:{B['navy']}; margin-right:4px; }}
.tag.hi {{ background:#fee2e2; color:#b91c1c; }} .tag.md {{ background:#fef3c7; color:#92400e; }} .tag.lo {{ background:#dcfce7; color:#166534; }}
.card {{ background:#fff; border:1px solid {B['mist']}; border-radius:10px; padding:12px 14px; margin-bottom:10px; }}
.alert {{ display:flex; gap:10px; align-items:flex-start; padding:10px 12px; border-left:4px solid {B['accent']}; background:#fff; border-radius:8px; margin-bottom:8px; }}
.alert .t {{ font-size:11px; color:#6b7280; min-width:88px; }}
.steps {{ display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }}
.step {{ background:#fff; border:1px solid {B['mist']}; border-radius:999px; padding:6px 12px; font-size:12px; }}
.step.on {{ background:{B['navy']}; color:#fff; border-color:{B['navy']}; }}
.arrow {{ color:#9ca3af; }}
.foot {{ font-size:11px; color:#6b7280; margin-top:10px; }}
.phone {{ width:390px; margin:0 auto; border:10px solid #111; border-radius:36px; overflow:hidden; background:{B['paper']}; }}
.layers {{ display:grid; grid-template-columns:120px 1fr; gap:8px; }}
.layer-name {{ background:{B['navy']}; color:#fff; border-radius:8px; padding:10px; font-size:13px; display:flex; align-items:center; justify-content:center; }}
.layer-boxes {{ display:flex; gap:8px; flex-wrap:wrap; }}
.box {{ background:#fff; border:1px solid {B['mist']}; border-radius:8px; padding:8px 12px; font-size:13px; }}
.box.app {{ border-color:{B['accent']}; border-width:2px; }}
</style>"""

_PRODUCTS = ["稳健一号", "量化增强二号", "商品 CTA 三号", "套利精选", "宏观对冲五号", "产业链六号"]


def _rng(seed: str) -> random.Random:
    return random.Random(sum(map(ord, seed)))


def _table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="xz"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def _pct(rng: random.Random, lo=-3.0, hi=3.0) -> str:
    v = rng.uniform(lo, hi)
    cls = "up" if v > 0 else "down"
    return f'<span class="{cls}">{v:+.2f}%</span>'


def _report_or_page(card: Card, rng: random.Random, client_view: bool) -> str:
    inds = card.indicators or ["净值", "回撤"]
    by_product = "资管产品" in card.scope_objects or not card.symbols
    key = "产品" if by_product else "品种"
    keys = _PRODUCTS if by_product else (card.symbols or ["螺纹钢", "热轧卷板", "铁矿石"])
    blob = " ".join(card.features + card.raw_features + [card.title])
    extra_cols = textutil.find_added_columns(blob)
    headers: list[str] = [key]
    for h in extra_cols + inds:
        if h and h not in headers:
            headers.append(h)
    if any("变动" in f or "较上一" in f for f in card.features) and "较上一交易日变动" not in headers:
        headers.insert(1, "较上一交易日变动")
    headers = headers[:8]
    if client_view:
        headers = [h for h in headers if h not in ("集中度", "杠杆", "保证金占用")]
    rows = []
    for k in keys[:6]:
        r = [k]
        for ind in headers[1:]:
            if ind in ("净值", "单位净值", "累计净值"):
                r.append(f"{rng.uniform(0.9, 1.6):.4f}")
            elif ind in ("回撤",):
                r.append(f"{rng.uniform(0.2, 6):.2f}%")
            elif ind in ("集中度", "杠杆"):
                r.append(f"{rng.uniform(5, 45):.1f}%")
            elif ind in ("基差", "价差"):
                r.append(f"{rng.uniform(-120, 180):+.0f}")
            elif ind in ("涨跌幅", "较上一交易日变动") or "变动" in ind:
                r.append(_pct(rng, -1.5, 1.5))
            elif "对标" in ind or "指数" in ind:
                r.append(f"沪深300 {_pct(rng, -1.2, 1.2)}")
            elif "盈亏" in ind or "超额" in ind:
                r.append(_pct(rng, -2.5, 2.5))
            else:
                r.append(f"{rng.uniform(10, 90):.1f}")
        rows.append(r)
    kpis = "".join(f'<div class="xz-kpi"><div class="n">{v}</div><div class="l">{l}</div></div>' for v, l in
                   ((f"{len(keys)} 只" if by_product else f"{len(keys)} 个", f"{key}范围"), ("T+1 16:45", "数据时点（结算后）"),
                    (f"{rng.randint(0, 3)}", "今日异常项"), ("已脱敏" if client_view else "内部版", "版本")))
    bar = (f'<div class="xz-bar"><input type="date" value="2026-09-08"><select><option>全部{key}</option>' + "".join(f"<option>{k}</option>" for k in keys[:6]) + "</select>"
           + '<button class="xz-btn">查询</button><button class="xz-btn ghost">导出 Excel</button><button class="xz-btn ghost">订阅每日推送</button>'
           + ('' if client_view else '<button class="xz-btn warn">切换到客户版</button>') + "</div>")
    alert = ""
    if "提醒" in " ".join(card.features) or card.req_type == "提醒":
        alert = f'<div class="alert"><span class="tag hi">异常</span><div><b>{keys[1]}</b> {inds[0]}较近 60 日均值偏离 2.3σ，已推送企业微信（09:32）</div></div>'
    return bar + f'<div class="xz-kpis">{kpis}</div>' + alert + _table(headers, rows)


def _alerts(card: Card, rng: random.Random) -> str:
    syms = card.symbols or ["螺纹钢", "沪铜", "PTA"]
    items = []
    levels = [("hi", "高"), ("md", "中"), ("lo", "低")]
    for i in range(5):
        s = syms[i % len(syms)]
        lv = levels[i % 3]
        items.append(f'<div class="alert"><span class="t">09:{30 + i * 7:02d}</span><span class="tag {lv[0]}">{lv[1]}</span>'
                     f'<div><b>{s}</b> {"基差" if "基差" in card.indicators else "价格"}异动 {rng.uniform(1, 4):+.1f}%，超过阈值；已推送企业微信，30 分钟内不重复</div></div>')
    settings = ('<div class="card"><b>提醒设置</b><div class="xz-bar" style="margin-top:8px"><span>阈值</span><input value="2.0%" size="6">'
                '<span>渠道</span><select><option>企业微信</option><option>邮件</option></select><span>夜盘</span><select><option>只推高等级</option><option>全部</option><option>不推</option></select>'
                '<button class="xz-btn">保存</button></div></div>')
    return settings + "".join(items)


def _impact(card: Card, rng: random.Random) -> str:
    steps = "".join(f'<span class="step{" on" if i < 3 else ""}">{s}</span>{"<span class=arrow>→</span>" if i < 4 else ""}'
                    for i, s in enumerate(["抓取公告", "解析结构化", "人工复核", "影响测算", "推送通知"]))
    notice = ('<div class="card"><b>公告解析结果</b>（待风控复核 ✅）<br>品种：<span class="tag">螺纹钢</span><span class="tag">热轧卷板</span> '
              '调整：保证金 8% → 12%，涨跌停 6% → 9%　生效：09-28 结算时起</div>')
    headers = ["产品 / 客户", "持仓（手）", "调整前保证金", "调整后保证金", "追加资金", "风险度", "建议"]
    rows = []
    for i, name in enumerate(_PRODUCTS[:4] + ["客户 <客户_1>", "客户 <客户_2>"]):
        pos = rng.randint(50, 800)
        before = pos * rng.uniform(3, 5) * 10000
        after = before * 1.5
        risk = rng.uniform(55, 98)
        tag = '<span class="tag hi">追保</span>' if risk > 90 else ('<span class="tag md">关注</span>' if risk > 75 else '<span class="tag lo">正常</span>')
        rows.append([name, str(pos), f"{before/10000:,.0f} 万", f"{after/10000:,.0f} 万", f"{(after-before)/10000:,.0f} 万", f"{risk:.0f}%", tag])
    draft = ('<div class="card"><b>客户通知草稿</b>（合规措辞，发送前审阅）<br>尊敬的客户：根据交易所公告，自 9 月 28 日结算时起螺纹钢、热轧卷板保证金标准调整为 12%。'
             '按您当前持仓测算需追加保证金约 __ 万元，请及时关注账户资金。<br><button class="xz-btn ghost">复制</button> <button class="xz-btn">提交合规审阅</button></div>')
    return f'<div class="steps">{steps}</div>' + notice + _table(headers, rows) + draft


def _api(card: Card) -> str:
    return ('<div class="card"><b>GET /api/v1/limit-check</b>　旁路只读，P95 &lt; 300ms<br><pre style="background:#f3f4f6;padding:8px;border-radius:6px">'
            '请求：{ "product": "稳健一号", "contract": "RB2601", "side": "buy", "lots": 300 }\n'
            '返回：{ "allowed": false, "max_lots": 180, "reasons": [\n'
            '  { "rule": "产品合同·单品种占比≤20%", "used": "17.8%", "after": "24.1%" },\n'
            '  { "rule": "交易所限仓·RB2601", "limit": 900, "held": 720 } ],\n'
            '  "suggest": "减仓 RB2510 120 手后可下满" }</pre></div>')


def render(card: Card, mobile: bool = False, client_view: bool = False) -> str:
    rng = _rng(card.title)
    t = card.req_type
    announcement = t == "数据" or "公告" in card.title or any("公告" in d for d in card.data_sources)
    if announcement:
        body = _impact(card, rng)
    elif t == "提醒":
        body = _alerts(card, rng)
    elif t == "接口":
        body = _api(card)
    else:
        body = _report_or_page(card, rng, client_view)
    who = html.escape(card.requester or "业务方")
    page = (f'<div class="xz-top"><h2>{html.escape(card.title)}</h2><span class="who">{who} · 原型 v0.1 · 假数据</span></div>'
            f'<div class="xz-wrap">{body}<div class="foot">需知 · 出样：本页由需求卡片自动生成，数据均为示例；业务确认后作为验收依据。</div></div>')
    if mobile:
        page = f'<div class="phone">{page}</div>'
    return f"<!doctype html><html><head><meta charset='utf-8'>{CSS}</head><body>{page}</body></html>"


def layers_html(layers: dict[str, list[str]]) -> str:
    rows = []
    for name in ("入口", "应用", "服务", "数据"):
        boxes = "".join(f'<div class="box{" app" if name == "应用" and i == 0 else ""}">{html.escape(b)}</div>' for i, b in enumerate(layers.get(name, [])))
        rows.append(f'<div class="layer-name">{name}</div><div class="layer-boxes">{boxes}</div>')
    return f"<!doctype html><html><head><meta charset='utf-8'>{CSS}</head><body style='background:#fff'><div class='layers'>{''.join(rows)}</div></body></html>"

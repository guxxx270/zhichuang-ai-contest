"""出样：把需求卡片变成一张可点的 HTML 原型页（假数据、公司 VI 风格），让业务"看得见需求"。
有底稿（上传 / 链接 / git）时：在底稿上改标题、插列、加标注；没有底稿时退回四套规则模板。
也提供 layers_html：定架用的分层架构图。全部为自包含 HTML，不依赖外网。"""
from __future__ import annotations

import html
import random
import re

from .. import config
from ..drafts import Draft, pick_draft, related_drafts, draft_relevance, is_visual_html, visual_drafts
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
_BENCHMARKS = ("中证1000", "中证2000", "沪深300", "中证500", "中证800", "上证50", "创业板指")


def _rng(seed: str) -> random.Random:
    return random.Random(sum(map(ord, seed)))


def _pick_benchmark(card: Card) -> str:
    blob = " ".join(card.features + card.raw_features + [card.title] + (card.indicators or []))
    for name in _BENCHMARKS:
        if name in blob:
            return name
    return "沪深300"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="xz"><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>'


def _pct(rng: random.Random, lo=-3.0, hi=3.0) -> str:
    v = rng.uniform(lo, hi)
    cls = "up" if v > 0 else "down"
    return f'<span class="{cls}">{v:+.2f}%</span>'


def _report_or_page(card: Card, rng: random.Random, client_view: bool, demand_text: str = "") -> str:
    from .. import textutil as _tu

    inds: list[str] = []
    for snip in _demand_snippets(card, demand_text):
        for col in _tu.find_added_columns(snip):
            _tu.merge_label(inds, col)
    by_product = bool(card.scope_objects)
    key = "名称"
    keys = list(card.symbols or card.scope_objects or [])[:6] or [f"对象{i}" for i in range(1, 5)]
    if not inds:
        inds = ["数值"]
    headers: list[str] = [key]
    for h in inds:
        if h and h not in headers:
            headers.append(h)
    headers = headers[:8]
    if client_view:
        headers = [h for h in headers if h not in _HIDE_CLIENT]
    rows = []
    for k in keys[:6]:
        r = [k]
        for _ind in headers[1:]:
            r.append(_fake_like_neighbors(r, rng))
        rows.append(r)
    kpis = "".join(f'<div class="xz-kpi"><div class="n">{v}</div><div class="l">{l}</div></div>' for v, l in
                   ((f"{len(keys)} 只" if by_product else f"{len(keys)} 个", f"{key}范围"), ("T+1 16:45", "数据时点（结算后）"),
                    (f"{rng.randint(0, 3)}", "今日异常项"), ("已脱敏" if client_view else "内部版", "版本")))
    chips = "".join(f'<span class="tag">{html.escape(f[:20])}</span>' for f in card.features[:5])
    bar = (f'<div class="xz-bar"><input type="date"><select><option>全部{key}</option>' + "".join(f"<option>{k}</option>" for k in keys[:6]) + "</select>"
           + '<button class="xz-btn">查询</button><button class="xz-btn ghost">导出</button></div>')
    if chips:
        bar += f'<div class="xz-bar">{chips}</div>'
    return bar + f'<div class="xz-kpis">{kpis}</div>' + _table(headers, rows)


def _card_blob(card: Card, demand_text: str = "") -> str:
    return " ".join(
        [demand_text or "", card.title or "", card.goal or "", card.req_type or ""]
        + list(card.features or [])
        + list(card.indicators or [])
        + list(card.raw_features or [])
    )


def _named_modules(blob: str) -> list[str]:
    """从原话抽出模块路径：【环境变量】、XX模块下。"""
    names: list[str] = []
    for m in re.finditer(r"【([^】]{2,20})】", blob or ""):
        n = m.group(1).strip()
        if n and n not in names:
            names.append(n)
    for m in re.finditer(r"([\u4e00-\u9fffA-Za-z0-9&]{2,16})模块", blob or ""):
        n = m.group(1).strip()
        if n and n not in names and n not in ("该", "本", "此", "相关"):
            names.append(n)
    return names


def _named_tabs(blob: str) -> list[str]:
    """只有原话里写了的 Tab 名才出现，不套默认「截面/时序」。"""
    tabs: list[str] = []
    for m in re.finditer(r"改名为[：:]\s*([^\s，。；；,;]{2,20})", blob or ""):
        n = re.sub(r"(?i)tab页?|页面$", "", m.group(1)).strip(" ：:，,")
        if n and n not in tabs:
            tabs.append(n)
    for m in re.finditer(r"[「\"“]([^」\"”]{2,16})[」\"”]\s*(?:tab|Tab)?页?", blob or ""):
        n = m.group(1).strip()
        if n and n not in tabs and "因子" not in n:
            tabs.append(n)
    for pat in (r"(\S{2,12})tab页", r"tab页[：:]\s*(\S{2,12})"):
        for m in re.finditer(pat, blob or "", flags=re.I):
            n = re.sub(r"(?i)tab页?", "", m.group(1)).strip(" ：:，,")
            if n and n not in tabs:
                tabs.append(n)
    return tabs[:6]


def _named_ids(blob: str) -> list[str]:
    """原话里的标识符（如 Factor_xxx、weight），不作业务假设。"""
    found = re.findall(r"\b[A-Za-z][A-Za-z0-9_]{2,40}\b", blob or "")
    skip = {
        "tab", "Tab", "TAB", "http", "https", "www", "html", "true", "false",
        "null", "None", "select", "input", "button", "div", "span", "pdf",
        "xlsx", "json", "utf", "UTF",
    }
    out: list[str] = []
    for x in found:
        if x in skip:
            continue
        if x not in out:
            out.append(x)
    return out[:8]


def _bar_svg(rng: random.Random, labels: list[tuple[str, float]]) -> str:
    max_w = max((v for _n, v in labels), default=1) or 1
    x0, gap, bw = 40, 36, 28
    bars = []
    for i, (name, w) in enumerate(labels):
        h = int(w / max_w * 120)
        x = x0 + i * (bw + gap)
        bars.append(
            f'<rect x="{x}" y="{150 - h}" width="{bw}" height="{h}" fill="#3b6fd8" rx="3"/>'
            f'<text x="{x + bw / 2}" y="168" text-anchor="middle" font-size="11" fill="#4b5563">{html.escape(name)}</text>'
            f'<text x="{x + bw / 2}" y="{144 - h}" text-anchor="middle" font-size="10" fill="#1f2937">{w:.1f}</text>'
        )
    return (
        '<svg viewBox="0 0 420 190" width="100%" height="190" style="background:#fafafa;border:1px solid #e5e7eb;border-radius:8px">'
        + "".join(bars)
        + "</svg>"
    )


def _sample_series(card: Card, rng: random.Random) -> list[tuple[str, float]]:
    labels = list(card.symbols or [])[:6] or list(card.indicators or [])[:6]
    if not labels:
        labels = [f"示例项{i}" for i in range(1, 6)]
    return [(lab, round(rng.uniform(5, 30), 1)) for lab in labels]


def _line_svg(rng: random.Random, caption: str) -> str:
    yvals = [40 + (hash(caption + str(i)) % 70) + rng.uniform(0, 12) for i in range(8)]
    mx, mn = max(yvals), min(yvals)
    span = max(mx - mn, 1)
    pts, dots = [], []
    for i, y in enumerate(yvals):
        x = 30 + i * 48
        yy = 150 - (y - mn) / span * 110
        pts.append(f"{x:.0f},{yy:.0f}")
        dots.append(f'<circle cx="{x:.0f}" cy="{yy:.0f}" r="3.5" fill="#c2410c"/>')
    return (
        '<svg viewBox="0 0 420 190" width="100%" height="190" style="background:#fafafa;border:1px solid #e5e7eb;border-radius:8px">'
        f'<polyline fill="none" stroke="#c2410c" stroke-width="2.5" points="{" ".join(pts)}"/>'
        + "".join(dots)
        + f'<text x="30" y="18" font-size="12" fill="#6b7280">{html.escape(caption)}</text>'
        "</svg>"
    )


def _feature_prototype(card: Card, rng: random.Random, client_view: bool = False, demand_text: str = "") -> str:
    """通用出样脚手架：只根据原话结构拼页面，不绑定某一业务域。"""
    blob = _card_blob(card, demand_text)
    title = html.escape(card.title or "需求原型")
    modules = _named_modules(blob)
    current = modules[-1] if modules else (card.title or "当前页")
    crumb_html = " / ".join(html.escape(c) for c in (modules or [card.title or "当前页"]))
    tabs = _named_tabs(blob)
    named_ids = _named_ids(blob)
    want_bar = bool(re.search(r"柱状|条形图|\bbar\b", blob, re.I))
    want_line = bool(re.search(r"折线|时序|走势|趋势图|\bline\b", blob, re.I))
    want_chart = want_bar or want_line or bool(re.search(r"图表|可视化|看板", blob))
    want_filter = bool(re.search(r"筛选|过滤|多选|全选|清空|模糊搜索|下拉", blob))
    series = _sample_series(card, rng)
    default_id = named_ids[0] if named_ids else ""

    nav = "".join(
        f'<a href="#" class="nav{" on" if n == current else ""}" '
        f'{"data-xz-inert=1" if n != current else ""}>{html.escape(n)}</a>'
        for n in (modules or [current])
    )
    brand = html.escape((modules[0] if modules else "内部系统")[:20])

    tabs_bar = ""
    if tabs:
        btns = [
            f'<button type="button" class="tab{" on" if i == len(tabs) - 1 else ""}" data-tab="p{i}">{html.escape(t)}</button>'
            for i, t in enumerate(tabs)
        ]
        tabs_bar = '<div class="tabs">' + "".join(btns) + "</div>"

    filters = []
    if named_ids:
        opts = "".join(
            f'<label class="opt"><input type="radio" name="nid" value="{html.escape(x)}" '
            f'{"checked" if x == default_id else ""}> {html.escape(x)}</label>'
            for x in named_ids
        )
        filters.append(f'<div class="boxf"><b>原话中的标识 / 默认项</b>{opts}</div>')
    if want_filter:
        opt_html = "".join(
            f'<label class="opt"><input type="checkbox" class="var" value="{html.escape(n)}" '
            f'{"checked" if i == 0 else ""}> {html.escape(n)}</label>'
            for i, (n, _w) in enumerate(series)
        )
        filters.append(
            '<div class="boxf"><b>筛选</b>'
            '<input class="search" id="varq" placeholder="搜索">'
            '<div class="acts"><button type="button" id="all">全选</button>'
            '<button type="button" id="none">清空</button></div>'
            f'<div id="varlist">{opt_html}</div></div>'
        )
    filter_html = f'<div class="filters">{"".join(filters)}</div>' if filters else ""

    panels = []
    if tabs:
        for i, t in enumerate(tabs):
            on = " on" if i == len(tabs) - 1 else ""
            prefer_line = bool(
                want_line and (re.search(r"折线|时序|走势|趋势", t) or (i == len(tabs) - 1 and not want_bar))
            )
            prefer_bar = bool(want_bar and (re.search(r"柱状|条形|分布", t) or (i == 0 and not prefer_line)))
            if prefer_bar:
                inner = _bar_svg(rng, series)
            elif prefer_line or (want_line and not want_bar):
                inner = _line_svg(rng, " · ".join(x for x in (default_id, t) if x))
            elif want_bar:
                inner = _bar_svg(rng, series)
            elif want_chart:
                inner = _bar_svg(rng, series)
            else:
                inner = f'<div class="hint">「{html.escape(t)}」内容区（按需求点示意，假数据）</div>'
            panels.append(
                f'<div id="p{i}" class="panel{on}">{inner}'
                f'<div class="hint">{html.escape(t)}</div></div>'
            )
    else:
        bits = []
        if want_bar:
            bits.append(_bar_svg(rng, series))
        if want_line:
            bits.append(_line_svg(rng, default_id or (card.title or "趋势")))
        if want_chart and not bits:
            bits.append(_bar_svg(rng, series))
        if not bits:
            bits.append(_report_or_page(card, rng, client_view, demand_text=demand_text))
        panels.append('<div class="panel on">' + "".join(bits) + "</div>")

    feat_lis = "".join(f"<li>{html.escape(f)}</li>" for f in (card.features or [])[:10])
    note_html = (
        f'<div class="hint">变更要点（摘自需求）：</div><ul class="feats">{feat_lis}</ul>'
        if card.features
        else ""
    )

    script = """
<script>
(function(){
  var tabs=document.querySelectorAll('.tab');
  function show(id){
    document.querySelectorAll('.panel').forEach(function(p){ p.classList.toggle('on', p.id===id); });
    tabs.forEach(function(t){ t.classList.toggle('on', t.getAttribute('data-tab')===id); });
  }
  tabs.forEach(function(t){ t.addEventListener('click', function(){ show(t.getAttribute('data-tab')); }); });
  var q=document.getElementById('varq');
  var list=document.getElementById('varlist');
  if(q && list) q.addEventListener('input', function(){
    var s=q.value.trim().toLowerCase();
    list.querySelectorAll('.opt').forEach(function(el){
      el.style.display = !s || el.textContent.toLowerCase().indexOf(s)>=0 ? '' : 'none';
    });
  });
  var all=document.getElementById('all'), none=document.getElementById('none');
  if(all && list) all.onclick=function(){ list.querySelectorAll('.var').forEach(function(c){c.checked=true;}); };
  if(none && list) none.onclick=function(){ list.querySelectorAll('.var').forEach(function(c){c.checked=false;}); };
})();
</script>
"""
    return f"""
<style>
.xz-mod {{ display:flex; min-height:520px; font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif; color:#1f2937; }}
.xz-side {{ width:168px; background:#1e3a6e; color:#fff; padding:12px 0; flex-shrink:0; }}
.xz-side .brand {{ padding:4px 16px 12px; font-weight:700; font-size:14px; opacity:.95; }}
.xz-side a.nav {{ display:block; color:#dbeafe; text-decoration:none; padding:8px 16px; font-size:13px; }}
.xz-side a.nav.on {{ background:#143067; color:#fff; border-left:3px solid #f59e0b; }}
.xz-main {{ flex:1; padding:14px 18px; background:#f4f6fa; }}
.xz-crumb {{ font-size:12px; color:#6b7280; margin-bottom:8px; }}
.xz-main h2 {{ margin:0 0 10px; font-size:18px; }}
.tabs {{ display:flex; gap:0; margin-bottom:12px; border-bottom:1px solid #d1d5db; }}
.tab {{ background:transparent; border:none; border-bottom:2px solid transparent; padding:8px 16px; cursor:pointer; font-size:13px; color:#4b5563; }}
.tab.on {{ color:#1e3a6e; border-bottom-color:#1e3a6e; font-weight:600; }}
.panel {{ display:none; }} .panel.on {{ display:block; }}
.filters {{ display:flex; gap:16px; flex-wrap:wrap; background:#fff; border:1px solid #e5e7eb; border-radius:10px; padding:12px; margin-bottom:12px; }}
.boxf {{ min-width:220px; flex:1; }}
.boxf b {{ display:block; font-size:12px; margin-bottom:6px; color:#374151; }}
.search {{ width:100%; border:1px solid #d1d5db; border-radius:6px; padding:5px 8px; font-size:12px; margin-bottom:6px; }}
.opt {{ display:block; font-size:12px; margin:3px 0; }}
.acts {{ margin:6px 0; }}
.acts button {{ font-size:11px; margin-right:6px; padding:3px 8px; border:1px solid #9ca3af; background:#fff; border-radius:4px; cursor:pointer; }}
.hint {{ font-size:12px; color:#4b5563; margin:4px 0; }}
.feats {{ font-size:12px; color:#6b7280; }}
</style>
<div class="xz-mod">
  <aside class="xz-side">
    <div class="brand">{brand}</div>
    {nav}
  </aside>
  <div class="xz-main">
    <div class="xz-crumb">{crumb_html}</div>
    <h2>{title}</h2>
    {tabs_bar}
    {filter_html}
    {"".join(panels)}
    {note_html}
  </div>
</div>
{script}
"""


def _alerts(card: Card, rng: random.Random) -> str:
    syms = card.symbols or [f"对象{i}" for i in range(1, 4)]
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
    blob = _card_blob(card)
    steps = "".join(
        f'<span class="step{" on" if i < 3 else ""}">{s}</span>{"<span class=arrow>→</span>" if i < 4 else ""}'
        for i, s in enumerate(["抓取来源", "解析结构化", "人工复核", "影响测算", "推送通知"])
    )
    tags = card.symbols[:4] if card.symbols else ["对象A", "对象B"]
    tag_html = "".join(f'<span class="tag">{html.escape(s)}</span>' for s in tags)
    notice = (
        f'<div class="card"><b>解析结果</b>（待复核）<br>范围：{tag_html} '
        f'调整：参数示例 8% → 12%　生效：待业务确认</div>'
    )
    if "保证金" in blob or "追保" in blob:
        headers = ["产品 / 客户", "持仓（手）", "调整前保证金", "调整后保证金", "追加资金", "风险度", "建议"]
        hi_tag = "追保"
    else:
        headers = ["对象", "规模", "调整前", "调整后", "差额", "风险度", "建议"]
        hi_tag = "需处理"
    rows = []
    names = list(card.symbols or [])[:4] or (_PRODUCTS[:4] + ["对象 <客户_1>", "对象 <客户_2>"])
    for name in names:
        pos = rng.randint(50, 800)
        before = pos * rng.uniform(3, 5) * 10000
        after = before * 1.5
        risk = rng.uniform(55, 98)
        tag = (
            f'<span class="tag hi">{hi_tag}</span>'
            if risk > 90
            else ('<span class="tag md">关注</span>' if risk > 75 else '<span class="tag lo">正常</span>')
        )
        rows.append(
            [name, str(pos), f"{before/10000:,.0f} 万", f"{after/10000:,.0f} 万",
             f"{(after-before)/10000:,.0f} 万", f"{risk:.0f}%", tag]
        )
    draft = (
        '<div class="card"><b>通知草稿</b>（发送前审阅）<br>尊敬的客户：根据最新公告/规则变更，相关参数已调整。'
        '按您当前情况测算影响约 __ ，请及时关注。<br>'
        '<button class="xz-btn ghost">复制</button> <button class="xz-btn">提交审阅</button></div>'
    )
    return f'<div class="steps">{steps}</div>' + notice + _table(headers, rows) + draft


def _api(card: Card) -> str:
    sample = (card.symbols or ["对象A"])[0]
    return (
        f'<div class="card"><b>GET /api/v1/check</b>　旁路只读，P95 &lt; 300ms<br>'
        f'<pre style="background:#f3f4f6;padding:8px;border-radius:6px">'
        f'请求：{{ "target": "{html.escape(sample)}", "action": "submit", "qty": 300 }}\n'
        f'返回：{{ "allowed": false, "max_qty": 180, "reasons": [\n'
        f'  {{ "rule": "规则示例·占比上限", "used": "17.8%", "after": "24.1%" }} ],\n'
        f'  "suggest": "调整后可提交" }}</pre></div>'
    )


_HIDE_CLIENT = ("集中度", "杠杆", "保证金占用")


def _fake_cell(ind: str, rng: random.Random, bench: str, bench_move: str) -> str:
    if ind in ("净值", "单位净值", "累计净值"):
        return f"{rng.uniform(0.9, 1.6):.4f}"
    if ind in ("回撤",):
        return f"{rng.uniform(0.2, 6):.2f}%"
    if ind in ("集中度", "杠杆"):
        return f"{rng.uniform(5, 45):.1f}%"
    if ind in ("基差", "价差"):
        return f"{rng.uniform(-120, 180):+.0f}"
    if ind in ("涨跌幅", "较上一交易日变动") or "变动" in ind:
        return _pct(rng, -1.5, 1.5)
    if "对标" in ind:
        return f"{html.escape(bench)} {bench_move}"
    if "盈亏" in ind or "超额" in ind:
        return _pct(rng, -2.5, 2.5)
    return f"{rng.uniform(10, 90):.1f}"


def _inject_banner(html_src: str, card: Card, draft: Draft, added: list[str], *, focus_note: str = "") -> str:
    who = html.escape(card.requester or "业务方")
    add_txt = "、".join(html.escape(x) for x in added) if added else "界面已按需求调整"
    focus = focus_note or f"聚焦本页：{draft.title or draft.name}"
    banner = (
        f'<div style="background:#111;color:#fff;padding:6px 12px;'
        f'font-family:system-ui,sans-serif;font-size:12px;line-height:1.4;">'
        f'<b>需知 · 出样</b>　原页改动　<code style="opacity:.9">{html.escape(draft.name)}</code>　'
        f'需求：{html.escape(card.title)}　{who}　已改：{add_txt}'
        f'<div style="opacity:.85;margin-top:2px;font-size:11px;">'
        f'{html.escape(focus)}　·　其余菜单/链接可点但不可进入（非本次需求范围）。</div></div>'
    )
    low = html_src.lower()
    if "<body" in low:
        return re.sub(r"(<body[^>]*>)", r"\1" + banner, html_src, count=1, flags=re.I)
    return banner + html_src


def _card_page_keys(card: Card) -> set[str]:
    keys: set[str] = set(card.keywords()) | set(card.indicators) | set(card.features) | {card.title, card.req_type}
    for f in card.features:
        keys.update(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{2,}", f or ""))
    return {k for k in keys if k and len(str(k)) >= 2}


def _file_stem(name: str) -> str:
    return re.sub(r"\.(html?|htm)$", "", name or "", flags=re.I)


def _offscope_labels(focus: Draft, drafts: list[Draft]) -> list[str]:
    labels: list[str] = []
    for d in drafts or []:
        if d.name == focus.name and d.title == focus.title:
            continue
        for x in (d.name, _file_stem(d.name), d.title, *d.headings[:4]):
            s = (x or "").strip()
            if len(s) >= 2 and s not in labels:
                labels.append(s)
    return labels


def _is_offscope_link(href: str, text: str, focus: Draft, off_labels: list[str]) -> bool:
    href = (href or "").strip()
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if not href or href in ("#", "#!") or href.lower().startswith("javascript:"):
        return bool(text) and any(lab == text or (len(lab) >= 2 and lab in text) for lab in off_labels)
    low = href.lower()
    if low.startswith("#"):
        return False
    focus_stem = _file_stem(focus.name).lower()
    focus_title = (focus.title or "").strip().lower()
    blob = f"{href} {text}".lower()
    if focus_stem and focus_stem in low:
        return False
    if focus_title and len(focus_title) >= 2 and focus_title == text.lower():
        return False
    for lab in off_labels:
        lab_l = lab.lower()
        if len(lab_l) < 2:
            continue
        if lab_l in blob or lab_l == text.lower():
            return True
    if re.search(r"\.(html?|htm|vue|jsx?|tsx?)([?#]|$)", low):
        return True
    if re.search(r"(^|/)(pages?|views?|routes?|menus?)/", low):
        return True
    if low.startswith(("http://", "https://", "//", "/", "./", "../")):
        return True
    return False


def _disable_offscope_nav(html_src: str, focus: Draft, drafts: list[Draft]) -> str:
    """不相干页面入口：看起来仍可点，但点不动，并提示非本次需求范围。"""
    off_labels = _offscope_labels(focus, drafts)
    tip = "非本次需求相关页面，预览中不可进入"

    def repl(m: re.Match[str]) -> str:
        pre, quote, href, post, inner = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        text = re.sub(r"<[^>]+>", "", inner or "")
        if not _is_offscope_link(href, text, focus, off_labels):
            # 同页或未知：保留外观，href 改 # 防跳走
            return f"<a{pre}href={quote}#{quote}{post}>{inner}</a>"
        attrs = pre + post
        if re.search(r"\bdata-xz-inert\b", attrs, flags=re.I):
            return f"<a{attrs}href={quote}#{quote}>{inner}</a>"
        return (
            f'<a{pre}href={quote}#{quote} data-xz-inert="1" title="{tip}" '
            f'style="cursor:pointer"{post}>{inner}</a>'
        )

    page = re.sub(
        r"<a(\s[^>]*?)href\s*=\s*([\"'])(.*?)\2([^>]*)>(.*?)</a>",
        repl,
        html_src or "",
        flags=re.I | re.S,
    )
    style = (
        "<style>a[data-xz-inert]{cursor:pointer!important}"
        ".xz-inert-toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);"
        "background:rgba(0,0,0,.82);color:#fff;padding:8px 14px;border-radius:8px;"
        "font:12px/1.4 system-ui,sans-serif;z-index:99999;opacity:0;pointer-events:none;"
        "transition:opacity .2s}.xz-inert-toast.on{opacity:1}</style>"
    )
    script = (
        "<script>(function(){"
        "function toast(msg){var el=document.querySelector('.xz-inert-toast');"
        "if(!el){el=document.createElement('div');el.className='xz-inert-toast';document.body.appendChild(el);}"
        "el.textContent=msg;el.classList.add('on');clearTimeout(el._t);"
        "el._t=setTimeout(function(){el.classList.remove('on');},1400);}"
        "document.addEventListener('click',function(e){"
        "var a=e.target&&e.target.closest&&e.target.closest('a');"
        "if(!a)return;e.preventDefault();e.stopPropagation();"
        "if(a.getAttribute('data-xz-inert'))toast('非本次需求相关，预览中不可进入');"
        "},true);"
        "document.addEventListener('submit',function(e){e.preventDefault();},true);"
        "})();</script>"
    )
    if re.search(r"</head\s*>", page, flags=re.I):
        page = re.sub(r"</head\s*>", style + "</head>", page, count=1, flags=re.I)
    else:
        page = style + page
    if re.search(r"</body\s*>", page, flags=re.I):
        page = re.sub(r"</body\s*>", script + "</body>", page, count=1, flags=re.I)
    else:
        page = page + script
    return page


def _replace_first_heading(html_src: str, title: str) -> str:
    def repl(m: re.Match[str]) -> str:
        return f"{m.group(1)}{html.escape(title)}{m.group(3)}"

    out, n = re.subn(r"(<h[12][^>]*>)(.*?)(</h[12]>)", repl, html_src, count=1, flags=re.I | re.S)
    if n:
        return out
    out, n = re.subn(r"(<title[^>]*>)(.*?)(</title>)", repl, html_src, count=1, flags=re.I | re.S)
    return out if n else html_src


def _split_row_cells(inner: str) -> list[str]:
    parts = re.split(r"(</(?:th|td)>)", inner or "", flags=re.I)
    cells: list[str] = []
    buf = ""
    for p in parts:
        buf += p
        if re.match(r"</(?:th|td)>$", p, flags=re.I):
            cells.append(buf)
            buf = ""
    if buf.strip():
        cells.append(buf)
    return cells


def _table_header_labels(table: str) -> tuple[list[str], bool]:
    th_headers = [_strip_cell(x) for x in re.findall(r"<th\b[^>]*>(.*?)</th>", table, flags=re.I | re.S)]
    if th_headers:
        return th_headers, True
    first = re.search(r"<tr\b[^>]*>(.*?)</tr>", table, flags=re.I | re.S)
    if not first:
        return [], False
    return [_strip_cell(x) for x in re.findall(r"<td\b[^>]*>(.*?)</td>", first.group(1), flags=re.I | re.S)], False


def _match_header_index(headers: list[str], name: str) -> int | None:
    name = (name or "").strip()
    if not name:
        return None
    for i, h in enumerate(headers):
        if h == name:
            return i
    for i, h in enumerate(headers):
        if len(name) >= 2 and (name in h or h in name):
            return i
    return None


def _demand_blob(card: Card, demand_text: str = "") -> str:
    return " ".join(
        [demand_text or "", card.title or "", card.goal or ""]
        + list(card.features or [])
        + list(card.raw_features or [])
        + list(card.indicators or [])
    )


def _demand_snippets(card: Card, demand_text: str = "") -> list[str]:
    """分条文本，避免拼成 blob 后「加一列X」把后面词吞进列名。"""
    bits: list[str] = []
    for x in (
        [demand_text or "", card.title or "", card.goal or ""]
        + list(card.features or [])
        + list(card.raw_features or [])
    ):
        s = (x or "").strip()
        if s and s not in bits:
            bits.append(s)
    return bits


def _mutate_draft_table(
    html_src: str,
    card: Card,
    rng: random.Random,
    client_view: bool,
    demand_text: str = "",
) -> tuple[str, list[str]]:
    """在原始 <table> 上做增列 / 删列 / 改列名（不另起说明块）。"""
    from .. import textutil as _tu

    snippets = _demand_snippets(card, demand_text)
    # 只认原话里「加一列/新增一列…」点名的列；不以标题词、指标词典往表里塞列
    to_add: list[str] = []
    for snip in snippets:
        for col in _tu.find_added_columns(snip):
            _tu.merge_label(to_add, col)
    to_del: list[str] = []
    to_rename: list[tuple[str, str]] = []
    for snip in snippets:
        for col in _tu.find_removed_columns(snip):
            _tu.merge_label(to_del, col)
        for pair in _tu.find_renamed_columns(snip):
            if pair not in to_rename and not any(
                _tu.same_label(pair[0], a) and _tu.same_label(pair[1], b) for a, b in to_rename
            ):
                to_rename.append(pair)
    if client_view:
        to_add = [w for w in to_add if not _tu.has_label(list(_HIDE_CLIENT), w)]
        for h in _HIDE_CLIENT:
            _tu.merge_label(to_del, h)

    # 去重保序（merge_label 已按大小写折叠去重）
    to_add = [w for w in to_add if w][:8]
    to_del = [d for d in to_del if d][:8]

    if not to_add and not to_del and not to_rename:
        return html_src, []

    m = re.search(r"(<table\b[^>]*>)(.*?)(</table>)", html_src, flags=re.I | re.S)
    if not m:
        return html_src, []

    table = m.group(0)
    headers, use_th = _table_header_labels(table)
    if not headers:
        return html_src, []

    changes: list[str] = []
    working = list(headers)

    # 1) 改名（先改名再删/加，按最终名对齐）
    for old, new in to_rename:
        idx = _match_header_index(working, old)
        if idx is None:
            continue
        if new in working and working[idx] != new:
            continue
        working[idx] = new
        changes.append(f"{old}→{new}")

    # 2) 删除
    drop: set[int] = set()
    for name in to_del:
        idx = _match_header_index(working, name)
        if idx is not None:
            drop.add(idx)
            changes.append(f"删列·{working[idx]}")
    if drop:
        working = [h for i, h in enumerate(working) if i not in drop]

    # 3) 新增：只加上面抽到的明确列名
    existing_set = {e for e in working if e}
    add_names = [
        w
        for w in to_add
        if not _tu.has_label(list(existing_set), w)
        and not any(
            _tu.label_key(w) == _tu.label_key(e)
            or (len(w) >= 2 and (_tu.label_key(w) in _tu.label_key(e) or _tu.label_key(e) in _tu.label_key(w)))
            for e in existing_set
        )
    ]

    for name in add_names:
        changes.append(f"加列·{name}")

    if not changes and not add_names and not drop and not to_rename:
        return html_src, []

    row_i = {"n": 0}

    def patch_row(row: re.Match[str]) -> str:
        inner = row.group(1)
        idx = row_i["n"]
        row_i["n"] += 1
        cells = _split_row_cells(inner)
        is_header = ("<th" in inner.lower()) or (not use_th and idx == 0)

        if drop:
            cells = [c for i, c in enumerate(cells) if i not in drop]

        if is_header and to_rename:
            for i, _h in enumerate(working[: len(cells)]):
                for old, new in to_rename:
                    if old in _strip_cell(cells[i]) or _strip_cell(cells[i]) == old:
                        if use_th or "<th" in cells[i].lower():
                            cells[i] = re.sub(
                                r"(<(?:th|td)\b[^>]*>)(.*?)(</(?:th|td)>)",
                                lambda mm, n=new: f"{mm.group(1)}{html.escape(n)}{mm.group(3)}",
                                cells[i],
                                count=1,
                                flags=re.I | re.S,
                            )
                        else:
                            cells[i] = re.sub(
                                r"(<td\b[^>]*>)(.*?)(</td>)",
                                lambda mm, n=new: f"{mm.group(1)}<b>{html.escape(n)}</b>{mm.group(3)}",
                                cells[i],
                                count=1,
                                flags=re.I | re.S,
                            )
                        break

        for name in add_names:
            if is_header:
                cells.append(f"<th>{html.escape(name)}</th>" if use_th else f"<td><b>{html.escape(name)}</b></td>")
            else:
                cells.append(f"<td>{_fake_like_neighbors(cells, rng)}</td>")

        return "<tr>" + "".join(cells) + "</tr>"

    new_table = re.sub(r"<tr\b[^>]*>(.*?)</tr>", patch_row, table, flags=re.I | re.S)
    if row_i["n"] == 0:
        return html_src, []
    return html_src[: m.start()] + new_table + html_src[m.end() :], changes


def _ensure_table_columns(html_src: str, card: Card, rng: random.Random, client_view: bool) -> tuple[str, list[str]]:
    """兼容旧调用：等价于表结构变更（增/删/改列）。"""
    return _mutate_draft_table(html_src, card, rng, client_view, demand_text="")


def _strip_cell(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _fake_like_neighbors(cells: list[str], rng: random.Random) -> str:
    """按同行已有单元格的形态造示意值，不依赖业务词典。"""
    samples = [_strip_cell(c) for c in cells if _strip_cell(c)]
    for s in reversed(samples):
        if re.search(r"%", s):
            return _pct(rng)
        if re.fullmatch(r"[-+]?\d+\.\d+", s.replace(",", "")):
            return f"{rng.uniform(0.5, 99):.{min(4, len(s.split('.')[-1]))}f}"
        if re.fullmatch(r"[-+]?\d+", s.replace(",", "")):
            return str(rng.randint(1, 99))
        if re.search(r"\d", s) and len(s) <= 24:
            return f"{rng.uniform(10, 90):.1f}"
    return f"{rng.uniform(10, 90):.1f}"


def _hide_client_columns(html_src: str) -> str:
    m = re.search(r"(<table\b[^>]*>)(.*?)(</table>)", html_src, flags=re.I | re.S)
    if not m:
        return html_src
    table = m.group(0)
    headers = [_strip_cell(x) for x in re.findall(r"<th[^>]*>(.*?)</th>", table, flags=re.I | re.S)]
    drop = {i for i, h in enumerate(headers) if h in _HIDE_CLIENT}
    if not drop:
        return html_src

    def filter_row(row: re.Match[str]) -> str:
        parts = re.split(r"(</(?:th|td)>)", row.group(1), flags=re.I)
        cells: list[str] = []
        buf = ""
        for p in parts:
            buf += p
            if re.match(r"</(?:th|td)>$", p, flags=re.I):
                cells.append(buf)
                buf = ""
        if buf.strip():
            cells.append(buf)
        kept = "".join(c for i, c in enumerate(cells) if i not in drop)
        return f"<tr>{kept}</tr>"

    new_table = re.sub(r"<tr\b[^>]*>(.*?)</tr>", filter_row, table, flags=re.I | re.S)
    return html_src[: m.start()] + new_table + html_src[m.end() :]


def _insert_in_body(html_src: str, snippet: str, *, where: str = "end") -> str:
    """把片段插入 body：start=开标签后，end=</body> 前。"""
    if not snippet:
        return html_src
    if where == "start" and re.search(r"<body\b[^>]*>", html_src, flags=re.I):
        return re.sub(r"(<body\b[^>]*>)", r"\1" + snippet, html_src, count=1, flags=re.I)
    if re.search(r"</body\s*>", html_src, flags=re.I):
        return re.sub(r"</body\s*>", snippet + "</body>", html_src, count=1, flags=re.I)
    return html_src + snippet


def _mini_bar_svg(caption: str, rng: random.Random) -> str:
    vals = [round(rng.uniform(8, 28), 1) for _ in range(5)]
    mx = max(vals) or 1
    bars = []
    for i, w in enumerate(vals):
        h = int(w / mx * 90)
        x = 24 + i * 52
        bars.append(
            f'<rect x="{x}" y="{110 - h}" width="28" height="{h}" fill="#3b6fd8" rx="3"/>'
            f'<text x="{x + 14}" y="128" text-anchor="middle" font-size="10" fill="#6b7280">{i + 1}</text>'
        )
    return (
        f'<div style="margin:10px 12px;padding:10px;background:#fafafa;border:1px solid #e5e7eb;border-radius:8px;">'
        f'<div style="font:12px system-ui,sans-serif;color:#374151;margin-bottom:6px;">{html.escape(caption)}</div>'
        f'<svg viewBox="0 0 280 140" width="100%" height="140">{"".join(bars)}</svg></div>'
    )


def _mini_line_svg(caption: str, rng: random.Random) -> str:
    yvals = [40 + (hash(caption + str(i)) % 50) + rng.uniform(0, 10) for i in range(7)]
    mx, mn = max(yvals), min(yvals)
    span = max(mx - mn, 1)
    pts = []
    for i, y in enumerate(yvals):
        x = 20 + i * 40
        yy = 120 - (y - mn) / span * 90
        pts.append(f"{x:.0f},{yy:.0f}")
    return (
        f'<div style="margin:10px 12px;padding:10px;background:#fafafa;border:1px solid #e5e7eb;border-radius:8px;">'
        f'<div style="font:12px system-ui,sans-serif;color:#374151;margin-bottom:6px;">{html.escape(caption)}</div>'
        f'<svg viewBox="0 0 300 140" width="100%" height="140">'
        f'<polyline fill="none" stroke="#c2410c" stroke-width="2.5" points="{" ".join(pts)}"/></svg></div>'
    )


def _section_block(title: str, body_html: str) -> str:
    return (
        f'<section class="xz-demand-section" style="margin:12px;padding:14px 16px;background:#fff;'
        f'border:1px solid #e5e7eb;border-radius:10px;box-shadow:0 1px 2px rgba(0,0,0,.04);'
        f'font-family:system-ui,\'PingFang SC\',\'Microsoft YaHei\',sans-serif;">'
        f'<h2 style="margin:0 0 10px;font-size:16px;color:#1e3a6e;">{html.escape(title)}</h2>'
        f"{body_html}</section>"
    )


def _extract_add_target(text: str) -> str:
    """从「增加/新增/加一个…」里抽出要加的对象名。"""
    t = (text or "").strip()
    m = re.search(
        r"(?:增加|新增|添加|加上|加一个|加个|加一列|加入)([^，。；;\n]{1,24})",
        t,
    )
    if not m:
        return ""
    name = m.group(1).strip(" 的了个一列「」\"“”")
    name = re.sub(r"^(一个|一行|一块|一栏)", "", name)
    return name[:24]


def _patch_draft_for_demand(
    html_src: str,
    card: Card,
    rng: random.Random,
    client_view: bool,
    demand_text: str = "",
) -> tuple[str, list[str]]:
    """在底稿 HTML 上真正改界面（表结构变更优先落在原始 table）。"""
    page, added = _mutate_draft_table(html_src, card, rng, client_view, demand_text=demand_text)
    changes = list(added)
    blob = _card_blob(card, demand_text)
    feats = list(card.features or [])
    if demand_text and demand_text.strip() and demand_text.strip() not in feats:
        feats = [demand_text.strip()] + feats

    def _mark(name: str) -> None:
        if name and name not in changes:
            changes.append(name)

    # —— 广告位 ——
    if re.search(r"广告|推广位|banner", blob, re.I) and "xz-demand-patch-ad" not in page:
        ad = (
            '<aside class="xz-demand-patch-ad" style="margin:12px;min-height:88px;padding:18px 16px;'
            'border:2px dashed #ea580c;border-radius:10px;background:linear-gradient(90deg,#fff7ed,#ffedd5);'
            'color:#9a3412;text-align:center;font:15px/1.4 system-ui,sans-serif;">'
            '<div style="font-weight:700;margin-bottom:4px;">广告位</div>'
            '<div style="font-size:12px;opacity:.85;">示意物料 · 可替换为真实投放</div></aside>'
        )
        page = _insert_in_body(page, ad, where="end")
        _mark("广告位")

    # —— Tab ——
    tabs = _named_tabs(blob)
    if not tabs:
        for f in feats:
            for m in re.finditer(r"([\u4e00-\u9fffA-Za-z0-9_]{2,12})\s*(?:tab|Tab)?页", f or ""):
                n = m.group(1)
                if n not in tabs and n not in ("本", "该", "此"):
                    tabs.append(n)
        tabs = tabs[:6]
    if tabs and "xz-demand-patch-tabs" not in page:
        btns = []
        panels = []
        for i, t in enumerate(tabs):
            on = i == len(tabs) - 1
            style = (
                "background:none;border:none;padding:8px 14px;cursor:pointer;font-size:13px;"
                + ("border-bottom:2px solid #1e3a6e;font-weight:600;color:#1e3a6e;" if on else "color:#4b5563;")
            )
            btns.append(
                f'<button type="button" data-xz-tab="t{i}" class="xz-tab{" on" if on else ""}" style="{style}">'
                f"{html.escape(t)}</button>"
            )
            show = "block" if on else "none"
            inner = _mini_line_svg(t, rng) if re.search(r"时序|走势|折线|趋势", t) else (
                _mini_bar_svg(t, rng) if re.search(r"柱状|截面|分布|图", t) else
                f'<div style="padding:16px;color:#4b5563;font-size:13px;">「{html.escape(t)}」内容区</div>'
            )
            panels.append(f'<div id="xz-t{i}" class="xz-tab-panel" style="display:{show};">{inner}</div>')
        bar = (
            '<div class="xz-demand-patch-tabs" style="margin:10px 12px;">'
            '<div style="display:flex;gap:0;border-bottom:1px solid #d1d5db;">'
            + "".join(btns)
            + "</div>"
            + "".join(panels)
            + "<script>(function(){var root=document.currentScript&&document.currentScript.parentElement;"
            "if(!root)return;root.querySelectorAll('[data-xz-tab]').forEach(function(b){"
            "b.addEventListener('click',function(){var id=b.getAttribute('data-xz-tab');"
            "root.querySelectorAll('.xz-tab-panel').forEach(function(p){p.style.display=p.id==='xz-'+id?'block':'none';});"
            "root.querySelectorAll('[data-xz-tab]').forEach(function(x){"
            "var on=x===b;x.style.borderBottom=on?'2px solid #1e3a6e':'none';"
            "x.style.fontWeight=on?'600':'400';x.style.color=on?'#1e3a6e':'#4b5563';});});});})();</script>"
            "</div>"
        )
        page = _insert_in_body(page, bar, where="start")
        _mark("Tab：" + " / ".join(tabs))

    # —— 筛选 ——
    if re.search(r"筛选|过滤|模糊搜索|多选|全选", blob) and "xz-demand-filter" not in page:
        filt = (
            '<div class="xz-demand-filter" style="margin:12px;padding:12px;background:#fff;border:1px solid #e5e7eb;'
            'border-radius:10px;font:13px system-ui,sans-serif;">'
            '<div style="font-weight:600;margin-bottom:8px;color:#1f2937;">筛选</div>'
            '<input placeholder="搜索" style="width:100%;max-width:280px;padding:6px 10px;border:1px solid #d1d5db;'
            'border-radius:6px;margin-bottom:8px;">'
            '<div style="display:flex;gap:8px;flex-wrap:wrap;">'
            '<button type="button" style="padding:4px 10px;border:1px solid #9ca3af;background:#fff;border-radius:4px;">全选</button>'
            '<button type="button" style="padding:4px 10px;border:1px solid #9ca3af;background:#fff;border-radius:4px;">清空</button>'
            '</div></div>'
        )
        page = _insert_in_body(page, filt, where="start")
        _mark("筛选区")

    # —— 按钮 / 入口 ——
    for f in feats:
        m = re.search(r"([\u4e00-\u9fffA-Za-z0-9_]{2,16})(按钮|入口|链接)", f or "")
        if not m:
            continue
        label = m.group(1).strip("的了一个")
        if not label or label in page:
            continue
        btn = (
            f'<button type="button" class="xz-demand-patch-btn" style="margin:8px 12px;padding:8px 16px;'
            f'border:none;background:#1e3a6e;color:#fff;border-radius:8px;'
            f'font:13px system-ui,sans-serif;cursor:pointer;">{html.escape(label)}</button>'
        )
        page = _insert_in_body(page, btn, where="end")
        _mark(label + m.group(2))

    # —— 图表线索 ——
    if re.search(r"柱状|条形|截面", blob) and "xz-demand-chart-bar" not in page and not tabs:
        page = _insert_in_body(
            page,
            '<div class="xz-demand-chart-bar">' + _mini_bar_svg("柱状图示意", rng) + "</div>",
            where="end",
        )
        _mark("柱状图")
    if re.search(r"折线|时序|走势|趋势图", blob) and "xz-demand-chart-line" not in page and not tabs:
        page = _insert_in_body(
            page,
            '<div class="xz-demand-chart-line">' + _mini_line_svg("走势图示意", rng) + "</div>",
            where="end",
        )
        _mark("走势图")

    # —— 其余「增加/新增…」：落成真实区块；「加一列」已在表上处理则跳过 ——
    col_names = set(added)
    from .. import textutil as _tu

    for col in _tu.find_added_columns(blob):
        col_names.add(col)
    for f in feats:
        target = _extract_add_target(f) or ""
        if not target and re.search(r"增加|新增|添加|加上|加一个", f or ""):
            target = re.sub(r"^(请|麻烦|帮忙)?", "", f or "")[:18]
        if not target:
            continue
        if target in col_names or any(c and (c in target or target in c) for c in col_names):
            continue
        if re.search(r"一列|一栏|列\b", f or "") or re.search(
            r"(加|增|添|删|去|移|改).{0,8}列|(把|将).{1,16}(改成|改为|改名)",
            f or "",
        ):
            continue  # 表结构只走原始 table
        if any(t in " ".join(changes) for t in (target, target[:4])):
            continue
        if target in page and len(target) >= 4:
            continue
        low_t = target
        if re.search(r"广告|推广", low_t):
            continue
        if re.search(r"图|表|走势|分布|看板", low_t) and "列" not in low_t:
            body = _mini_bar_svg(target, rng) if re.search(r"柱|截面|分布", low_t) else _mini_line_svg(target, rng)
            page = _insert_in_body(page, _section_block(target, body), where="end")
        elif re.search(r"筛选|搜索|过滤", low_t):
            continue
        elif re.search(r"按钮|入口|链接", low_t):
            continue
        else:
            body = (
                f'<div style="display:grid;gap:8px;">'
                f'<div style="padding:12px;background:#f8fafc;border-radius:8px;color:#334155;font-size:13px;">'
                f'{html.escape(target)} · 示意内容</div>'
                f'<div style="display:flex;gap:8px;flex-wrap:wrap;">'
                f'<button type="button" style="padding:6px 12px;border:1px solid #1e3a6e;background:#1e3a6e;'
                f'color:#fff;border-radius:6px;font-size:12px;">确认</button>'
                f'<button type="button" style="padding:6px 12px;border:1px solid #9ca3af;background:#fff;'
                f'border-radius:6px;font-size:12px;">取消</button></div></div>'
            )
            page = _insert_in_body(page, _section_block(target, body), where="end")
        _mark(target)

    # 标题轻量对齐需求（若原页有 h1）
    if card.title and len(card.title) >= 2:
        page = _replace_first_heading(page, card.title)

    return page, changes


def _is_static_browser_html(html_src: str) -> bool:
    """浏览器不跑 Vue/React 时能否看见界面（真 table/form，而非 el-table 组件标签）。"""
    s = html_src or ""
    if not s.strip():
        return False
    fw = len(re.findall(r"<(?:el-|a-|van-|router-|i-|n-)[a-z0-9-]*\b", s, re.I))
    native_table = bool(re.search(r"(?is)<table\b", s)) and bool(re.search(r"(?is)<t[hd]\b", s))
    native_ctrl = len(re.findall(r"(?is)<(input|select|button|textarea|form)\b", s))
    if native_table or native_ctrl >= 2:
        return True
    # 满屏组件标签、没有原生控件 → iframe 里几乎是白的
    if fw >= 2:
        return False
    return is_visual_html(s)


def _extract_vue_template(text: str) -> str:
    m = re.search(r"(?is)<template\b[^>]*>(.*)</template>", text or "")
    return (m.group(1) if m else "") or ""


def _column_labels_from_markup(tpl: str) -> list[str]:
    """从 Element/Ant Vue 表列标签抽出表头文字。"""
    labels: list[str] = []

    def _add(lab: str) -> None:
        lab = (lab or "").strip()
        if not lab or lab.startswith("{{") or lab in labels:
            return
        if len(lab) > 40:
            return
        labels.append(lab)

    for m in re.finditer(
        r"<(?:el-table-column|a-table-column|vxe-column|vxe-table-column)\b([^>]*?)/?>",
        tpl or "",
        re.I,
    ):
        attrs = m.group(1) or ""
        hit = False
        for attr in ("label", "title", "header"):
            am = re.search(rf"""(?:^|\s):?{attr}\s*=\s*['"]([^'"]+)['"]""", attrs, re.I)
            if not am:
                continue
            raw = am.group(1).strip()
            lit = re.match(r"""^['"]([^'"]+)['"]$""", raw)
            _add(lit.group(1) if lit else raw)
            hit = True
            break
        if not hit:
            pm = re.search(r"""\bprop\s*=\s*['"]([^'"]+)['"]""", attrs, re.I)
            if pm:
                _add(pm.group(1))
    return labels[:24]


def _vue_sfc_to_static_html(path: str, text: str, page_title: str = "") -> str | None:
    """把 Vue SFC 转成可 iframe 的静态 HTML；转不出真表格则返回 None。"""
    from pathlib import Path as _P

    tpl = _extract_vue_template(text)
    if not tpl.strip():
        return None
    title = page_title or _P(path).stem

    # 模板里已有原生 table
    if re.search(r"(?is)<table\b", tpl) and re.search(r"(?is)<t[hd]\b", tpl):
        body = re.sub(r"(?is)</?template\b[^>]*>", "", tpl)
        # 去掉只会空白的组件壳，保留 table
        body = re.sub(r"(?is)</?(?:el-|a-|van-|router-)[a-z0-9-]*\b[^>]*>", "", body)
        page = (
            f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<title>{html.escape(title)}</title>{CSS}</head><body>"
            f'<div class="xz-wrap"><h2>{html.escape(title)}</h2>{body}</div>'
            f"</body></html>"
        )
        return page if _is_static_browser_html(page) else None

    cols = _column_labels_from_markup(tpl)
    if len(cols) < 2:
        return None

    th = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body_rows = []
    for i in range(5):
        tds = "".join(
            f"<td>{html.escape(str(round(1.0 + i * 0.01 + j * 0.1, 4) if j else f'产品{i+1}'))}</td>"
            for j, _c in enumerate(cols)
        )
        body_rows.append(f"<tr>{tds}</tr>")
    table = (
        f'<table class="xz"><thead><tr>{th}</tr></thead>'
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )
    page = (
        f"<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>{CSS}</head><body>"
        f'<div class="xz-wrap"><h2>{html.escape(title)}</h2>{table}</div>'
        f"</body></html>"
    )
    return page


def render_from_drafts(
    card: Card,
    drafts: list[Draft],
    mobile: bool = False,
    client_view: bool = False,
    demand_text: str = "",
) -> str | None:
    """只复刻有真实界面且与需求相关的静态底稿，并按需求做可见改动。
    用户上传的底稿可弱匹配；Git 里与需求无关或不可见的 HTML 一律不用。"""
    usable = [d for d in visual_drafts(drafts) if _is_static_browser_html(d.html or "")]
    if not usable:
        return None
    keys = _card_page_keys(card)
    uploaded = [d for d in usable if d.source in ("upload", "sample", "url")]
    pool = related_drafts(usable, keys, limit=3)
    if not pool and uploaded:
        pool = related_drafts(uploaded, keys, limit=3, allow_unrelated_fallback=True)
    if not pool:
        # 仓库 Vue→静态表：关键词可能对不上文件名，但上游已按表头/路径选过
        git_tables = [
            d
            for d in usable
            if d.source == "git"
            and re.search(r"(?is)<table\b", d.html or "")
            and _is_static_browser_html(d.html or "")
        ]
        if git_tables:
            pool = git_tables[:3]
    if not pool:
        return None
    draft = pick_draft(pool, keys)
    if not draft or not (draft.html or "").strip():
        # pick_draft 在全不相关时可能仍返回第一名；保证有表就用第一张
        draft = pool[0] if pool else None
    if not draft or not (draft.html or "").strip():
        return None
    if draft.source == "git" and draft_relevance(draft, keys) <= 0:
        # 仓库 Vue→静态表已由上游筛过；允许弱相关，避免只剩黑条横幅
        if not (
            re.search(r"(?is)<table\b", draft.html or "")
            and _is_static_browser_html(draft.html or "")
        ):
            return None
    if not _is_static_browser_html(draft.html):
        return None
    rng = _rng(card.title + draft.name)
    page = draft.html
    if not re.search(r"<html", page, flags=re.I):
        page = f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(draft.title or card.title)}</title></head><body>{page}</body></html>"
    page, added = _patch_draft_for_demand(page, card, rng, client_view, demand_text=demand_text)
    if client_view:
        page = _hide_client_columns(page)
    score = draft_relevance(draft, keys)
    others = [d for d in usable if d.name != draft.name]
    focus_note = f"聚焦本页：{draft.title or draft.name}"
    if score <= 0 and draft.source in ("upload", "sample", "url"):
        focus_note += "（按你上传的底稿改）"
    elif others:
        focus_note += f"；另有 {len(others)} 个不相干页未展开"
    page = _inject_banner(page, card, draft, added, focus_note=focus_note)
    page = _disable_offscope_nav(page, draft, list(usable))
    page = _finish_view(page, mobile=mobile)
    return page


def render_from_repos(card: Card, repos: list, demand_text: str = "") -> str | None:
    """仓库里仅当能转成浏览器可看的静态 HTML 时才复刻；裸 Vue 组件标签不算。"""
    from ..drafts import parse_draft

    html_drafts: list[Draft] = []
    for repo in repos or []:
        for f in getattr(repo, "files", []) or []:
            path = getattr(f, "path", "") or ""
            kind = getattr(f, "kind", "") or ""
            text = getattr(f, "text", "") or ""
            note = getattr(f, "note", "") or ""
            if note in ("spa-shell", "iconfont"):
                continue
            if kind == "html" or path.lower().endswith((".html", ".htm")):
                d = parse_draft(path, text, source="git")
                if _is_static_browser_html(d.html):
                    html_drafts.append(d)
            elif kind == "frontend" and path.lower().endswith((".vue", ".tsx", ".jsx")):
                static = _vue_sfc_to_static_html(path, text)
                if static and _is_static_browser_html(static):
                    html_drafts.append(parse_draft(path + ".preview.html", static, source="git"))
            elif kind == "frontend" and ("<template" in text or "<html" in text.lower()):
                static = _vue_sfc_to_static_html(path, text)
                if static and _is_static_browser_html(static):
                    html_drafts.append(parse_draft(path + ".preview.html", static, source="git"))
    if not html_drafts:
        return None
    keys = _card_page_keys(card)
    focused = related_drafts(html_drafts, keys, limit=3)
    if not focused:
        focused = _pick_repo_table_drafts(html_drafts, card, keys, demand_text=demand_text, limit=3)
    if not focused:
        return None
    return render_from_drafts(card, focused, mobile=False, client_view=False, demand_text=demand_text)


def _pick_repo_table_drafts(
    drafts: list[Draft],
    card: Card,
    keys: set[str],
    *,
    demand_text: str = "",
    limit: int = 3,
) -> list[Draft]:
    """关键词对不上时：用路径/表头弱匹配（如 incomeRisk + 夏普 ↔ Sortino 需求）。"""
    blob_demand = " ".join(
        [card.title or "", demand_text or "", *list(card.features or []), *list(card.indicators or [])]
    )
    want_col = bool(re.search(r"新增|加一列|加列|增加.{{0,6}}列|Sortino|夏普|回撤", blob_demand, re.I))

    def score(d: Draft) -> int:
        s = draft_relevance(d, keys)
        name = (d.name or "").lower()
        html_src = d.html or ""
        for hint in (
            "risk", "income", "factor", "perf", "nav", "sharpe", "return",
            "observe", "pool", "cta", "收益", "风险", "因子", "表现", "净值", "夏普",
        ):
            if hint in name or hint in html_src[:3000].lower():
                s += 2
        if re.search(r"sortino", blob_demand, re.I) and ("夏普" in html_src or "sharpe" in html_src.lower()):
            s += 6
        if want_col and re.search(r"(?is)<table\b", html_src):
            s += 3
        return s

    ranked = sorted(drafts, key=lambda d: (-score(d), d.name))
    good = [d for d in ranked if score(d) > 0]
    if good:
        return good[:limit]
    table_ones = [d for d in ranked if re.search(r"(?is)<table\b", d.html or "")]
    if want_col and table_ones:
        return table_ones[:1]
    return []


def _finish_view(page: str, mobile: bool = False) -> str:
    if mobile:
        page = re.sub(
            r"(<body[^>]*>)(.*?)(</body>)",
            lambda m: f"{m.group(1)}<div class='phone' style='width:390px;margin:0 auto;border:10px solid #111;border-radius:36px;overflow:hidden'>{m.group(2)}</div>{m.group(3)}",
            page,
            count=1,
            flags=re.I | re.S,
        )
        if "<style" not in page.lower():
            page = page.replace("</head>", f"{CSS}</head>") if "</head>" in page.lower() else CSS + page
    if not page.lower().lstrip().startswith("<!doctype"):
        page = "<!doctype html>\n" + page
    return page


def apply_prototype_view(html_src: str, mobile: bool = False, client_view: bool = False) -> str:
    """对已生成的原型底稿做手机版 / 客户版变换（不调模型）。"""
    page = html_src or ""
    if client_view:
        page = _hide_client_columns(page)
    return _finish_view(page, mobile=mobile)


def _extract_html(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    m = re.search(r"```(?:html)?\s*(.*?)```", raw, re.I | re.S)
    if m:
        raw = m.group(1).strip()
    if "<html" in raw.lower() or "<body" in raw.lower() or "<table" in raw.lower() or "<svg" in raw.lower():
        if not raw.lower().lstrip().startswith("<!doctype"):
            if raw.lower().lstrip().startswith("<html"):
                raw = "<!doctype html>\n" + raw
            elif "<body" in raw.lower():
                raw = f"<!doctype html><html><head><meta charset='utf-8'></head>{raw}</html>"
        if _is_source_code_dump(raw):
            return ""
        return raw
    return ""


def _is_source_code_dump(page: str) -> bool:
    """模型偶发把仓库 .ts/.js 接口层整段贴进「原型」——这种不算可出样界面。"""
    if not page or len(page) < 80:
        return False
    # 去掉 script/style 后再看正文，避免误伤内联脚本很少的正常页
    stripped = re.sub(r"(?is)<script\b[^>]*>.*?</script>", " ", page)
    stripped = re.sub(r"(?is)<style\b[^>]*>.*?</style>", " ", stripped)
    text = re.sub(r"<[^>]+>", " ", stripped)
    signals = 0
    if re.search(r"\bimport\s+.+\s+from\s+['\"]", text):
        signals += 2
    if re.search(r"\bexport\s+(default\s+)?(class|async\s+function|function|const)\b", text):
        signals += 2
    if re.search(r"\b(this\.http|HttpService|responseType\s*:\s*['\"]blob['\"])\b", text):
        signals += 2
    if text.count("async ") >= 4 and re.search(r"\bawait\s+this\.", text):
        signals += 2
    if re.search(r"\b(require\(|module\.exports|from\s+['\"]@/)", text):
        signals += 1
    ui = len(re.findall(r"<(?:table|button|input|form|nav|thead|select|label)\b", stripped, re.I))
    # 大段源码特征明显、几乎没有表单/表格控件 → 判定为抄源码
    return signals >= 3 and ui < 2


def _style_classes(html_src: str) -> set[str]:
    return {c for c in re.findall(r'class\s*=\s*["\']([^"\']+)["\']', html_src or "", flags=re.I) for c in c.split() if c}


def _style_block(html_src: str) -> str:
    m = re.search(r"(?is)<style\b[^>]*>(.*?)</style>", html_src or "")
    return re.sub(r"\s+", " ", (m.group(1) if m else "")).strip()


def _preserves_draft_style(page: str, draft: Draft) -> bool:
    """模型输出必须明显沿用底稿样式，否则判失败并回退规则复刻。"""
    if not page or not draft or not (draft.html or "").strip():
        return False
    draft_classes = _style_classes(draft.html)
    page_classes = _style_classes(page)
    if draft_classes:
        overlap = len(draft_classes & page_classes) / max(1, len(draft_classes))
        if overlap < 0.5:
            return False
    draft_css = _style_block(draft.html)
    page_css = _style_block(page)
    if len(draft_css) >= 40:
        # 抽若干 CSS 片段，至少一半要出现在输出里
        chunks = [draft_css[i : i + 24] for i in range(0, min(len(draft_css), 240), 24)]
        chunks = [c for c in chunks if len(c.strip()) >= 12]
        if chunks:
            hit = sum(1 for c in chunks if c in page_css)
            if hit < max(1, len(chunks) // 2):
                return False
    headers = [h for h in (draft.table_headers or [])[:4] if h]
    if headers and sum(1 for h in headers if h in page) < max(1, (len(headers) + 1) // 2):
        return False
    return True


def _html_ok(page: str, card: Card, draft: Draft) -> bool:
    if not page or len(page) < 80:
        return False
    low = page.lower()
    if "<html" not in low and "<body" not in low and "<table" not in low:
        return False
    markers = [draft.name, draft.title, *(draft.table_headers[:3]), card.title]
    markers = [m for m in markers if m and len(m) >= 2]
    if markers and not any(m in page for m in markers):
        return False
    if not _preserves_draft_style(page, draft):
        return False
    return True


def adapt_draft_with_llm(card: Card, drafts: list[Draft], llm, repos: list | None = None, demand_text: str = "") -> tuple[str | None, Draft | None]:
    """有可复刻底稿则在原 HTML 上改；SPA 空壳 / 无底稿则按需求+仓库生成完整原型。"""
    if getattr(llm, "mode", "") != "api":
        return None, None
    from ..drafts import draft_context_for_llm, _trim_html, redact_material
    from ..llm import load_prompt
    from ..privacy import Redactor
    from .. import textutil as _tu
    import json

    mapping: dict[str, str] = {}   # 材料脱敏标签 → 原文；模型输出后还原
    keys = set(card.keywords()) | set(card.indicators) | set(card.features) | {card.title, card.req_type}
    usable = visual_drafts(drafts)
    draft = pick_draft(usable, keys) if usable else None
    system = load_prompt("prototype_adapt") or (
        "有可复刻静态底稿时在原 HTML 上改；SPA 空壳不算底稿，须按需求生成完整可交互 HTML。只输出 HTML。"
    )
    spa_notes = [d.note for d in (drafts or []) if d.note]
    snippets = _demand_snippets(card, demand_text)
    add_cols: list[str] = []
    del_cols: list[str] = []
    rename_cols: list[tuple[str, str]] = []
    for snip in snippets:
        for col in _tu.find_added_columns(snip):
            _tu.merge_label(add_cols, col)
        for col in _tu.find_removed_columns(snip):
            _tu.merge_label(del_cols, col)
        for pair in _tu.find_renamed_columns(snip):
            if pair not in rename_cols and not any(
                _tu.same_label(pair[0], a) and _tu.same_label(pair[1], b) for a, b in rename_cols
            ):
                rename_cols.append(pair)
    payload = {
        "需求标题": card.title,
        "需求类型": card.req_type,
        "本次原话": demand_text or "",
        "功能点": card.features,
        "原表表头": (draft.table_headers if draft else []),
        "明确要加的列": add_cols,
        "明确要删的列": del_cols,
        "明确要改名的列": [{"from": a, "to": b} for a, b in rename_cols],
        "提出方": card.requester,
        "使用者": card.users,
        "渠道": card.channels,
        "主底稿": draft.name if draft else "",
        "有现有材料": bool(drafts or repos),
        "材料备注": spa_notes,
    }
    if draft:
        payload["硬性要求"] = (
            "下面「底稿完整HTML」是唯一视觉基准：保留全部 style/class/布局，只按需求做最小改动。"
            "禁止重画成另一套页面。"
            "改表时只动原 <table>：加列只加「明确要加的列」里写出的名字，说加一列就只加一列；"
            "不要把标题、页面名、指标词典里的词当成新列；原表已有的列不要再加一份。"
        )
        payload["底稿完整HTML"] = redact_material(_trim_html(draft.html, 28000), mapping)
        if repos:
            payload["现有系统材料"] = draft_context_for_llm(
                [],
                list(repos or []),
                prefer=None,
                html_limit=2000,
                code_limit=12000,
                keywords=keys,
                mapping=mapping,
            )
    else:
        payload["硬性要求"] = (
            "没有可复刻的静态页面（可能是 Vue/React SPA 入口或仅有后端仓库）。"
            "必须按功能点生成完整自包含 HTML 原型：把 Tab、筛选、表格、按钮都画出来，不要只出说明条。"
            "仓库源码（.ts/.js Api、HttpService、import/export）只作字段/模块命名参考，"
            "禁止把源码整段贴进页面或包在 pre 里当原型。"
            "不相干菜单可保留外观但点击不跳转。"
        )
        if drafts or repos:
            payload["现有系统材料"] = draft_context_for_llm(
                list(drafts or []),
                list(repos or []),
                prefer=None,
                html_limit=8000,
                code_limit=22000,
                keywords=keys,
                mapping=mapping,
            )
        else:
            payload["说明"] = "用户未提供 HTML 或 Git，请仅根据需求卡片自行设计一版合理原型页。"
    try:
        llm.current_purpose = "出样改稿"
        raw = llm.chat(system, user=json.dumps(payload, ensure_ascii=False), temperature=0.1)
    except Exception:
        return None, draft
    page = _extract_html(raw)
    if mapping and page:
        page = Redactor.restore(page, mapping)   # 材料里被隐盾替换的标签还原成原文
    if draft:
        if not _html_ok(page, card, draft):
            return None, draft
        rng = _rng(card.title + (draft.name or ""))
        page, cols = _mutate_draft_table(page, card, rng, client_view=False, demand_text=demand_text)
        banner_cols = cols or add_cols[:5]
        if "需知" not in page[:800]:
            page = _inject_banner(page, card, draft, banner_cols)
        return page, draft
    if not page or ("<html" not in page.lower() and "<body" not in page.lower() and "<svg" not in page.lower() and "<table" not in page.lower()):
        return None, None
    if _is_source_code_dump(page):
        return None, None
    if "需知" not in page[:800]:
        who = html.escape(card.requester or "业务方")
        banner = (
            f'<div style="background:#111;color:#fff;padding:6px 12px;font-size:12px;">'
            f'<b>需知 · 出样</b>　按需求生成原型（无静态底稿可复刻）　{html.escape(card.title)}　{who}</div>'
        )
        if "<body" in page.lower():
            page = re.sub(r"(<body[^>]*>)", r"\1" + banner, page, count=1, flags=re.I)
        else:
            page = banner + page
    return page, None


def _generated_page(card: Card, client_view: bool = False, demand_text: str = "") -> str:
    rng = _rng(card.title)
    t = card.req_type
    blob = _card_blob(card, demand_text)
    announcement = t == "数据" or "公告" in card.title or any("公告" in d for d in card.data_sources)
    # 无静态底稿时：有模块/Tab/图/筛选等界面线索 → 按原话拼页面；否则用通用报表模板
    wants_ui = bool(
        _named_modules(blob)
        or _named_tabs(blob)
        or _named_ids(blob)
        or re.search(
            r"柱状|条形|折线|时序|走势|趋势图|图表|筛选|过滤|多选|全选|tab|Tab|模块|看板|可视化",
            blob,
            re.I,
        )
    )
    if announcement:
        body = _impact(card, rng)
    elif t == "提醒":
        body = _alerts(card, rng)
    elif t == "接口":
        body = _api(card)
    elif wants_ui:
        body = _feature_prototype(card, rng, client_view=client_view, demand_text=demand_text)
    else:
        body = _report_or_page(card, rng, client_view, demand_text=demand_text)
    who = html.escape(card.requester or "业务方")
    page = (
        f'<div class="xz-top"><h2>{html.escape(card.title)}</h2>'
        f'<span class="who">{who} · 原型 v0.1 · 假数据 · {html.escape(card.engine)}</span></div>'
        f'<div class="xz-wrap">{body}'
        f'<div class="foot">需知 · 出样：本页按需求生成可点原型（无静态底稿可复刻）；数据均为示例。</div></div>'
    )
    return f"<!doctype html><html><head><meta charset='utf-8'>{CSS}</head><body>{page}</body></html>"


def render(
    card: Card,
    mobile: bool = False,
    client_view: bool = False,
    drafts: list[Draft] | None = None,
    llm=None,
    source_html: str | None = None,
    repos: list | None = None,
    demand_text: str = "",
) -> str:
    """出样：有静态底稿则在原页上改；SPA/无 HTML 则按需求生成可点原型。"""
    if source_html:
        return apply_prototype_view(source_html, mobile=mobile, client_view=client_view)

    usable = visual_drafts(drafts)
    rule_adapted = (
        render_from_drafts(card, usable, mobile=mobile, client_view=client_view, demand_text=demand_text)
        if usable
        else None
    )

    # 有 API 时优先让模型在底稿上改（校验不通过则退回规则改页，禁止整页重画）
    if usable and llm is not None and getattr(llm, "mode", "") == "api":
        adapted, _ = adapt_draft_with_llm(card, list(drafts or []), llm, repos=repos, demand_text=demand_text)
        if adapted:
            if client_view:
                adapted = _hide_client_columns(adapted)
            return _finish_view(adapted, mobile=mobile)

    if rule_adapted:
        return rule_adapted

    if repos:
        repo_page = render_from_repos(card, repos, demand_text=demand_text)
        if repo_page:
            if client_view:
                repo_page = _hide_client_columns(repo_page)
            return _finish_view(repo_page, mobile=mobile)

    if llm is not None and getattr(llm, "mode", "") == "api":
        adapted, _ = adapt_draft_with_llm(card, list(drafts or []), llm, repos=repos, demand_text=demand_text)
        if adapted:
            if client_view:
                adapted = _hide_client_columns(adapted)
            return _finish_view(adapted, mobile=mobile)

    page = _generated_page(card, client_view=client_view, demand_text=demand_text)
    if mobile:
        page = _finish_view(page, mobile=True)
    return page


def render_source(
    card: Card,
    drafts: list[Draft] | None = None,
    llm=None,
    repos: list | None = None,
    demand_text: str = "",
) -> str:
    """生成不含手机框/客户版裁剪的原型源 HTML（供缓存，视图切换不再调模型）。"""
    usable = visual_drafts(drafts)
    rule_base = (
        render_from_drafts(card, usable, mobile=False, client_view=False, demand_text=demand_text)
        if usable
        else None
    )

    if usable and llm is not None and getattr(llm, "mode", "") == "api":
        adapted, _ = adapt_draft_with_llm(card, list(drafts or []), llm, repos=repos, demand_text=demand_text)
        if adapted:
            return adapted if adapted.lower().lstrip().startswith("<!doctype") else "<!doctype html>\n" + adapted

    if rule_base:
        return rule_base
    if repos:
        repo_page = render_from_repos(card, repos, demand_text=demand_text)
        if repo_page:
            return repo_page if repo_page.lower().lstrip().startswith("<!doctype") else "<!doctype html>\n" + repo_page
    if llm is not None and getattr(llm, "mode", "") == "api":
        adapted, _ = adapt_draft_with_llm(card, list(drafts or []), llm, repos=repos, demand_text=demand_text)
        if adapted:
            return adapted if adapted.lower().lstrip().startswith("<!doctype") else "<!doctype html>\n" + adapted
    return _generated_page(card, client_view=False, demand_text=demand_text)


def layers_html(layers: dict[str, list[str]]) -> str:
    rows = []
    for name in ("入口", "应用", "服务", "数据"):
        boxes = "".join(f'<div class="box{" app" if name == "应用" and i == 0 else ""}">{html.escape(b)}</div>' for i, b in enumerate(layers.get(name, [])))
        rows.append(f'<div class="layer-name">{name}</div><div class="layer-boxes">{boxes}</div>')
    return f"<!doctype html><html><head><meta charset='utf-8'>{CSS}</head><body style='background:#fff'><div class='layers'>{''.join(rows)}</div></body></html>"

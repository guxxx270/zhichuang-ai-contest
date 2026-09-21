"""定架：技术栈 / 架构建议 + IT 决策清单（选项 / 推荐 / 理由 / 影响，生成 ADR）+ 复用发现（对照公司系统目录）\n+ 数据地图（需求里的每个数据项定位到 系统 · 数据域 · 接口 · 时效，汇总可复用 / 需申请 / 需新建）。
有页面底稿时：优先建议在现有 HTML / 前端工程上改，并把底稿写入分层图与 ADR。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .. import knowledge
from ..drafts import Draft, RepoBundle, summarize, visual_drafts
from .intake import Card


@dataclass
class Reuse:
    system: str
    owner: str
    matched: list[str]
    integration: str
    maturity: str

    @property
    def score(self) -> int:
        return len(self.matched)


@dataclass
class Decision:
    id: str
    topic: str
    question: str
    options: list[tuple[str, str, str]]     # (方案, 优点, 代价)
    recommended: str
    reason: str
    impact: str


@dataclass
class DataSource:
    """数据地图的一行：需求里的一个数据项 → 从哪个系统的哪个数据域、走哪个接口取，时效与状态。"""
    need: str                 # 需求里的数据项（原话口径）
    system: str               # 来源系统；"—" 表示目录里没有
    domain: str               # 数据域
    interface: str            # 接口 / 取数方式
    freshness: str            # 实时 / T+1 / 日终 / 事件
    status: str               # 可复用 / 需申请 / 改动受控 / 需新建 / 时效不符
    owner: str                # 负责团队
    note: str = ""


@dataclass
class Architecture:
    stack: list[tuple[str, str]]            # (层, 建议)
    layers: dict[str, list[str]]            # 画图用：入口 / 应用 / 服务 / 数据
    reuse: list[Reuse]
    coverage_line: str
    decisions: list[Decision]
    adr_md: str = ""
    engine: str = "规则"
    draft_names: list[str] = field(default_factory=list)
    data_map: list[DataSource] = field(default_factory=list)
    data_map_line: str = ""
    data_map_stats: dict[str, int] = field(default_factory=dict)


AFFINITY = {"报表": "报表平台", "提醒": "消息推送中心", "流程": "OA 移动门户", "接口": "交易系统"}   # 需求类型 → 天然承载平台

STATUS_ORDER = {"需新建": 0, "时效不符": 1, "需计算": 2, "改动受控": 3, "需申请": 4, "可复用": 5}


def _realtime_level(card: Card, text: str) -> int:
    """0 = 日终够用；1 = 偏好实时源（分钟级 / 事件后限时）；2 = 必须实时（下单前 / 盘中 / 秒级）。"""
    if card.frequency == "实时" or re.search(r"盘中|下单前|报单前|秒级", text):
        return 2
    if card.frequency in ("分钟级", "事件后限时") or re.search(r"实时", text):
        return 1
    return 0


def _data_needs(card: Card) -> list[tuple[str, str]]:
    """需求里的数据项：(数据项, 来源) —— 先取抽到的数据来源（source），再补指标（indicator，更细、能定位到数据域）。"""
    needs: list[tuple[str, str]] = []
    for ds in card.data_sources:
        if ds and all(ds != n for n, _ in needs):
            needs.append((ds, "source"))
    for ind in card.indicators:
        if ind and not any(ind in n for n, _ in needs):
            needs.append((ind, "indicator"))
    return needs[:14]


def _interface_for(system: knowledge.System, realtime: bool) -> tuple[str, str]:
    """挑一个最合适的接口：实时需求优先 SDK / REST，否则优先视图 / 文件；返回 (接口名, 状态)。"""
    if not system.interfaces:
        return system.integration or "待确认", "需申请"
    def rank(i: knowledge.Interface) -> tuple[int, int]:
        pref = {"REST": 0, "SDK": 1, "SQL 视图": 2, "SQL 表": 2, "文件": 3, "配置": 4} if realtime else \
               {"SQL 视图": 0, "SQL 表": 0, "文件": 1, "REST": 2, "配置": 3, "SDK": 4}
        return (0 if i.ready else 1, pref.get(i.type, 5))
    best = sorted(system.interfaces, key=rank)[0]
    if best.ready:
        status = "可复用"
    elif "受控" in best.status or "审批" in best.status:
        status = "改动受控"
    else:
        status = "需申请"
    return best.name, status


def build_data_map(card: Card, text: str) -> tuple[list[DataSource], str, dict[str, int]]:
    """数据地图：把需求里的每个数据项定位到「系统 · 数据域 · 接口 · 时效」，并汇总可复用 / 需申请 / 需新建。
    纯规则：关键词命中数据域 keywords，实时需求优先实时数据域；目录里没有的标为需新建 / 外部接入。"""
    level = _realtime_level(card, text)
    realtime = level >= 1
    rows: list[DataSource] = []
    for need, origin in _data_needs(card):
        best: tuple[int, knowledge.System, knowledge.Domain] | None = None
        for s in knowledge.systems():
            name_hit = 2 if s.name in need else 0
            for d in s.domains:
                hits = sum(len(k) for k in d.keywords if k and k in need)
                if hits == 0 and not name_hit:
                    continue
                score = hits + name_hit * 3 + (3 if (realtime and d.realtime) else 0) + (1 if (not realtime and not d.realtime) else 0)
                if best is None or score > best[0]:
                    best = (score, s, d)
        if best is None:
            if origin == "indicator":
                rows.append(DataSource(need, "—", "—", "—", "—", "需计算", "本需求",
                                       "目录里没有现成字段，由本需求按数据字典口径计算"))
            else:
                ext = bool(re.search(r"第三方|外部|研究员维护|交易所日报|手工", need))
                rows.append(DataSource(need, "—", "—", "—", "—", "需新建", "待定",
                                       "目录中无此数据源，需外部接入或手工维护表" if ext else "目录中无此数据源，需新建采集 / 落库"))
            continue
        _, s, d = best
        iface, status = _interface_for(s, realtime)
        note = ""
        if level >= 2 and not d.realtime and "事件" not in d.freshness:
            status, note = "时效不符", f"需求要盘中 / 下单前可用，该域为{d.freshness}；需改用实时源或降级为日终口径"
        rows.append(DataSource(need, s.name, d.name, iface, d.freshness, status, s.owner, note))
    rows.sort(key=lambda r: STATUS_ORDER.get(r.status, 9))
    systems = [r.system for r in rows if r.system != "—"]
    stats = {
        "systems": len(dict.fromkeys(systems)),
        "domains": len({(r.system, r.domain) for r in rows if r.system != "—"}),
        "reuse": sum(1 for r in rows if r.status == "可复用"),
        "apply": sum(1 for r in rows if r.status in ("需申请", "改动受控")),
        "new": sum(1 for r in rows if r.status in ("需新建", "时效不符")),
        "calc": sum(1 for r in rows if r.status == "需计算"),
    }
    if not rows:
        line = "原话里没抽到明确的数据项，数据来源待问清后再定位。"
    else:
        line = (f"本需求涉及 {stats['systems']} 个系统、{stats['domains']} 个数据域："
                f"接口可复用 {stats['reuse']} 项、需申请 / 受控 {stats['apply']} 项、需新建或时效不符 {stats['new']} 项"
                + (f"、需本需求计算 {stats['calc']} 项" if stats["calc"] else ""))
        owners = list(dict.fromkeys(r.owner for r in rows if r.owner not in ("", "待定", "本需求")))
        if owners:
            line += f"；涉及团队：{'、'.join(owners[:5])}"
        line += "。"
    return rows, line, stats


def discover_reuse(card: Card, text: str) -> list[Reuse]:
    hay = text + " " + " ".join(card.features) + " " + " ".join(card.data_sources) + " " + " ".join(card.channels) + " " + card.req_type
    out = []
    host = AFFINITY.get(card.req_type)
    if card.req_type == "数据" or "公告" in hay:
        host = "公告采集服务" if "公告" in hay else "数据仓库"
    if re.search(r"下单|报单|指令|拒单", hay) and "公告" not in hay:
        host = "交易系统"
    for s in knowledge.systems():
        matched = [c for c in s.capabilities if c in hay]
        if s.name == host:
            matched = [f"承载{card.req_type}类需求"] + matched
        if matched:
            out.append(Reuse(s.name, s.owner, matched, s.integration, s.maturity))
    out.sort(key=lambda r: (-(r.system == host), -r.score))
    return out


def _coverage_line(card: Card, reuse: list[Reuse]) -> str:
    if not reuse:
        return "目录中无可复用系统，需新建。"
    top = reuse[0]
    others = "、".join(r.system for r in reuse[1:4])
    verb = "可作为承载平台" if top.score >= 3 else "可部分复用"
    line = f"「{top.system}」{verb}（命中：{'、'.join(top.matched)}）"
    if others:
        line += f"；数据与渠道复用 {others}"
    line += "。建议在现有系统上加模块而非新建。" if top.score >= 3 else "。核心逻辑需新建服务，外围复用。"
    return line


def suggest_stack(
    card: Card,
    text: str,
    reuse: list[Reuse],
    drafts: list[Draft] | None = None,
    repos: list[RepoBundle] | None = None,
) -> tuple[list[tuple[str, str]], dict[str, list[str]]]:
    t = card.req_type
    realtime = card.frequency in ("实时", "分钟级") or bool(re.search(r"实时|盘中|下单前", text))
    stack: list[tuple[str, str]] = []
    drafts = drafts or []
    repos = repos or []
    if drafts:
        names = "、".join(d.name for d in drafts[:4])
        cols = "、".join(dict.fromkeys(h for d in drafts for h in d.table_headers))[:80] or "（底稿无表头）"
        stack.append(("前端底稿", f"以已有页面为底：{names}；现有列：{cols}。按需求增量改 UI，避免整页重做"))
        stack.append(("改动面", "保留底稿布局与 VI；新增列 / 筛选 / 文案落在既有表格与工具条上，变更可 diff"))
    if repos:
        n = sum(len(r.files) for r in repos)
        sample = "、".join(f.path for r in repos for f in r.files[:6])
        stack.append((
            "代码仓库",
            f"已读 {len(repos)} 个仓库共约 {n} 个源文件，前后端一并理解。"
            f"优先在现有模块上改（例：{sample}{'…' if sample else '见目录'}），对齐现有页面与接口，避免平行新建一套",
        ))
    if t == "报表":
        stack += [("承载", "报表平台：新建数据集 + 模板，定时任务在结算文件到齐后触发"), ("计算", "SQL 视图 / Python 任务计算新增列与指标，写入报表数据集")]
    elif t == "页面":
        stack += [("前端", "内部 Web（Vue / React）" + ("，H5 嵌入 OA 移动门户" if "手机端可看" in card.nonfunctional else "")),
                  ("后端", "Python / Java 服务 + REST API，权限走权限中心")]
    elif t == "提醒":
        stack += [("检测", "规则引擎 + 定时 / 事件任务（按交易日历）"), ("推送", "消息推送中心：企业微信为主，频控与去重")]
    elif t == "接口":
        stack += [("服务", "旁路查询服务（只读），API 网关鉴权限流")]
    elif t == "数据":
        stack += [("采集", "公告采集服务 / 定时任务拉取源数据"), ("处理", "解析 + 校验 + 落库；非结构化文本可用公司 AI 平台解析，人工复核后生效")]
    else:
        stack += [("流程", "OA 流程引擎建模，表单 + 审批 + 留痕")]
    stack.append(("数据", "行情服务（实时）+ 数据仓库（日终）" if realtime else "数据仓库日终视图（T+1）"))
    if "客户" in card.scope_objects:
        stack.append(("安全", "客户字段脱敏（隐盾规则）；对外内容合规审阅留痕"))
    if re.search(r"公告|通知文案|解释|解析", text):
        stack.append(("AI", "公司 AI 平台（OpenAI 兼容 API）做非结构化解析 / 文案生成，输出需人工复核"))
    app_layer = [card.title] + [f for f in card.features[:3]]
    if drafts:
        app_layer = [f"底稿·{d.title or d.name}" for d in drafts[:3]] + app_layer
    if repos:
        app_layer = [f"仓·{r.name}" for r in repos[:2]] + app_layer
    layers = {
        "入口": card.channels or (["现有页面改造"] if (drafts or repos) else ["网页"]),
        "应用": app_layer,
        "服务": [r.system for r in reuse[:4]] or (
            [f"现仓·{repos[0].name}"] if repos else ["新建服务"]
        ),
        "数据": card.data_sources or ["数据仓库"],
    }
    return stack, layers


def build_decisions(
    card: Card,
    text: str,
    reuse: list[Reuse],
    drafts: list[Draft] | None = None,
    repos: list[RepoBundle] | None = None,
) -> list[Decision]:
    ds: list[Decision] = []
    drafts = drafts or []
    repos = repos or []
    top = reuse[0] if reuse else None
    if drafts or repos:
        names = "、".join([*(d.name for d in drafts[:2]), *(r.name for r in repos[:2])])
        ds.append(Decision(
            "D0", "基于现有代码/底稿改 vs 新建",
            f"已提供现有材料（{names}{'…' if (len(drafts) + len(repos)) > 2 else ''}），在现仓/现页上改还是另起新服务？",
            [
                ("基于现有仓库与页面增量改", "对齐现有架构与交互，验收可对照 diff", "受原结构约束"),
                ("保留现仓作参考、模块重建", "交互/接口可重做", "工时高，联调面大"),
                ("忽略现仓、按模板新建", "实现快", "与现网不一致，迁移成本高"),
            ],
            "基于现有仓库与页面增量改",
            "已加载源码/底稿，优先复用路由、API 与组件",
            "工时与验收方式",
        ))
    ds.append(Decision("D1", "新建 vs 复用", "在现有系统上加模块，还是新建独立服务？",
                       [(f"复用「{top.system}」加模块" if top else "复用现有平台", "省 30%～50% 工时，权限与运维现成", "受该系统排期与改动窗口约束"),
                        ("新建独立服务", "不受现有系统约束，迭代快", "多一套部署与运维，权限要重接")],
                       (f"复用「{top.system}」" if top and top.score >= 2 else "新建独立服务"),
                       (f"目录命中 {top.score} 项能力，成熟度「{top.maturity}」" if top else "无可复用系统"), "工时 ±30%；影响上线时间"))
    if card.data_sources:
        ds.append(Decision("D2", "数据来源与时点", "数据从哪取、以哪个时点为准？",
                           [("数据仓库日终（T+1）", "稳定、口径统一、成本低", "不能盘中看"), ("行情服务 / 交易系统实时", "盘中可用", "需订阅额度、并发与稳定性要求高"),
                            ("混合：盘中实时 + 日终归档", "两者兼顾", "两套口径要对账")],
                           "行情服务 / 交易系统实时" if re.search(r"实时|盘中|下单前", text) else "数据仓库日终（T+1）",
                           "按原话的时效要求判断" + ("（提到实时 / 盘中）" if re.search(r"实时|盘中|下单前", text) else "（未提实时）"), "架构与成本量级"))
    if card.req_type == "提醒" or re.search(r"提醒|预警|推送|通知", text):
        ds.append(Decision("D3", "批处理 vs 实时检测", "检测按分钟轮询、日终批处理，还是事件驱动？",
                           [("日终批处理", "最简单", "只能次日发现"), ("分钟级轮询", "够用且简单", "延迟几分钟"), ("事件驱动（行情 / 公告推送触发）", "秒级", "接入复杂、误报需治理")],
                           "分钟级轮询" if not re.search(r"公告|秒级", text) else "事件驱动（行情 / 公告推送触发）", "首版求稳，二期升级", "工时 +3～8 人天"))
        ds.append(Decision("D4", "推送渠道", "提醒走什么渠道？", [("企业微信", "触达快、可模板消息", "夜间打扰"), ("邮件", "留痕好", "看得慢"), ("企业微信 + 邮件双发", "兼顾", "重复")],
                           "企业微信" if "企业微信" in card.channels or not card.channels else "、".join(card.channels[:2]), "复用消息推送中心，频控由平台配置", "小"))
    if card.req_type in ("页面", "报表") or "客户" in card.scope_objects or re.search(r"权限|本人|只能看|自己管理|谁能看", text):
        ds.append(Decision("D5", "权限模型", "谁能看什么：按角色、按产品、按人？",
                           [("角色 + 产品维度（权限中心现成）", "现成、审计友好", "细粒度需配置"), ("自建权限表", "灵活", "重复建设、审计弱")],
                           "角色 + 产品维度（权限中心现成）", "投资经理只看本人产品是常见诉求，权限中心已支持", "工时 -2 人天"))
    if re.search(r"公告|解析|通知文案|解释|识别", text):
        ds.append(Decision("D6", "是否使用 AI 平台", "非结构化解析 / 文案生成用规则还是大模型？",
                           [("规则 + 模板", "确定性强", "覆盖不全、维护累"), ("公司 AI 平台 + 人工复核", "覆盖广、省人工", "需复核环节与评测"), ("两者结合：规则硬判、模型建议", "兼顾", "多一层逻辑")],
                           "两者结合：规则硬判、模型建议", "涉及资金数字的解析必须有人复核", "合规分 +，工时 +3 人天"))
    if re.search(r"下单|报单|指令|阻断|拒单", text):
        ds.append(Decision("D7", "是否改交易链路", "只做旁路提示，还是改交易系统做阻断？",
                           [("旁路提示（只读）", "不动交易链路、审批简单、可快速上线", "不能强制拦截"), ("交易系统事前风控增强（阻断）", "强约束", "变更审批、灰度、回退要求高，周期长")],
                           "旁路提示（只读）", "首版先解释与测算，阻断纳入二期与交易系统排期", "工时相差 2～3 倍"))
    if "客户" in card.scope_objects or "对外" in text:
        ds.append(Decision("D8", "对外内容与部署", "客户可见内容如何管控？", [("内网生成、人工发送", "最安全", "多一步人工"), ("系统直发客户", "省事", "需合规审阅自动化与更严的脱敏")],
                           "内网生成、人工发送", "首版先保证脱敏与审阅，直发放二期", "合规风险"))
    return ds


def data_map_markdown(rows: list[DataSource], line: str = "") -> list[str]:
    if not rows:
        return []
    md = ["## 数据地图（数据从哪取）", line, "", "| 数据项 | 来源系统 | 数据域 | 接口 / 取数方式 | 时效 | 状态 | 负责 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r.need} | {r.system} | {r.domain} | {r.interface} | {r.freshness} | {r.status}{'（' + r.note + '）' if r.note else ''} | {r.owner} |")
    return md + [""]


def adr_markdown(
    card: Card,
    decisions: list[Decision],
    drafts: list[Draft] | None = None,
    repos: list[RepoBundle] | None = None,
    data_map: list[DataSource] | None = None,
    data_map_line: str = "",
) -> str:
    today = date.today().isoformat()
    md = [f"# ADR · {card.title}", f"日期：{today}　状态：待评审　生成：需知", ""]
    md += data_map_markdown(data_map or [], data_map_line)
    if drafts or repos:
        md += ["## 现有材料", summarize(drafts or [], repos or []), ""]
        for r in (repos or [])[:2]:
            md.append(f"### 仓库 {r.name}")
            md.append("目录摘录：" + "；".join(r.tree[:12]))
            md.append("重点文件：" + "、".join(f.path for f in r.files[:8]))
            md.append("")
    for d in decisions:
        md += [f"## {d.id} {d.topic}", f"**问题**：{d.question}", "", "**选项**："]
        md += [f"- {o[0]}：优点 —— {o[1]}；代价 —— {o[2]}" for o in d.options]
        md += ["", f"**建议**：{d.recommended}　**理由**：{d.reason}　**影响**：{d.impact}", ""]
    return "\n".join(md)


def build_architecture(
    card: Card,
    text: str,
    drafts: list[Draft] | None = None,
    repos: list[RepoBundle] | None = None,
) -> Architecture:
    drafts = drafts or []
    repos = repos or []
    usable = visual_drafts(drafts)
    reuse = discover_reuse(card, text)
    stack, layers = suggest_stack(card, text, reuse, drafts=usable, repos=repos)
    decisions = build_decisions(card, text, reuse, drafts=usable, repos=repos)
    data_map, map_line, map_stats = build_data_map(card, text)
    if data_map:
        # 分层图的数据层改用数据地图定位到的系统；目录里没有的数据项单独标"待接入"
        located = list(dict.fromkeys(r.system for r in data_map if r.system != "—"))
        missing = [r.need.split("（")[0] for r in data_map if r.system == "—" and r.status == "需新建"]
        layers["数据"] = (located + [f"待接入·{m}" for m in missing[:2]]) or layers["数据"]
        for d in decisions:
            if d.id == "D2":
                d.reason += "；数据地图：" + "，".join(
                    f"{r.need.split('（')[0]}←{r.system}（{r.freshness}）" for r in data_map[:4] if r.system != "—"
                )
                if map_stats.get("new"):
                    d.impact += f"；{map_stats['new']} 项数据需新建或换实时源"
    line = _coverage_line(card, reuse)
    if usable or repos:
        line = f"已加载材料：{summarize(drafts, repos)}。" + line
    arch = Architecture(
        stack, layers, reuse, line, decisions,
        draft_names=[*(d.name for d in usable), *(r.name for r in repos)],
        data_map=data_map, data_map_line=map_line, data_map_stats=map_stats,
    )
    arch.adr_md = adr_markdown(card, decisions, drafts=usable, repos=repos, data_map=data_map, data_map_line=map_line)
    return arch

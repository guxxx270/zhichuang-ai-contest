# 需知 · 需求分析智能体（Demo v0.4）

> 一堆话进来，一份能开工的需求出去。
> 团队赛方案乙（技术部视角）的 Demo。微信三句话、一段会议纪要、一封邮件、一页 Word 扔进去，它先出一版需求，然后告诉你：要和业务确认哪几件事、这活多复杂、大概多少人天、哪几处要 IT 拍板，顺手把可点的原型页画出来。

## 启动

- **Windows**：双击 `启动需知.bat`（首次自动建虚拟环境并装依赖），浏览器打开即可。需要 **Python 3.10+**（推荐 3.12）。若 PATH 里的 `python` 是 3.7，脚本会改用 `py -3.12`。
- Mac：双击 `启动需知.command`（首次自动建虚拟环境并装依赖），浏览器打开即可。
- **企微入口**：双击 `启动企微入口.command`，之后在企业微信里 @需知 发需求即可（见下文「企微入口」）。
- **REST API / MCP**：双击 `启动接口.command` 起 REST（http://127.0.0.1:8770/docs）；`python mcp_server.py` 起 MCP（见下文「接口」）。
- 手动（Mac/Linux）：`pip install -r requirements.txt && streamlit run app.py`
- 手动（Windows PowerShell，在本目录执行）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

- 命令行看全流程：`python run_demo.py 1`（1～5 为五个样例；请用上面虚拟环境里的 python）
- 测试：`pytest -q tests`

不配置 Key 可以整理需求、写单、出原型，并查看**规则模板与历史记录参考**。配置模型后增加独立的大模型估时；未配置或调用失败只影响模型结果，不影响其他参考。页面只展示真实模型；**开工默认调用所选模型**，未填 Key 时需求初稿走规则。**页面底稿 / Git 仓库**：可上传 HTML，或粘贴多个文件/整仓链接；整仓会浅克隆并抽取前后端源码摘要，模型据此问清、估时、定架、出样（复刻现页再改）。**口述**仍可用（注意厂商语音服务合规）。API Key 仅会话有效。

## 六项技能（v0.3：六项都有——对账落在「一本账」里）

| 技能 | 做什么 | 引擎 |
|---|---|---|
| **问清** | 抽需求卡片；按「期货 IT 需求追问知识库」+ 通用检查项生成**待确认清单**（影响高 / 中 / 低，附"不问会怎样"与默认假设）；一键生成给业务的确认消息；业务答复后重算 | 规则 + 模型润色 |
| **写单** | **双版需求单**：业务版一页纸（可签字）+ 技术版（用户故事、Given/When/Then 验收、数据字典、接口草案、非功能），技术条目回链原话 | 规则模板 + 模型润色 |
| **估量** | **三方参考并列展示**：独立模型分析、规则模板参考、历史记录。模型按本条需求和已提供材料拆分工作，分别估「以前（人工开发）」「现在（AI协同）」的区间与参考值，附逐项理由和证据；无有效代码依据时标为粗估。规则参考展示自己的公式、任务与前提；历史记录列明当时估算、实际工时及开发方式。无 Key 仍能看规则与历史；三路不自动加权混合，仅有效模型数字进入模型工时汇总 | 模型分析 + 独立规则参考 + 历史检索 |
| **定架** | 技术栈建议、分层架构图、**复用发现**（对照公司系统目录）、**数据地图**（需求里的每个数据项定位到 系统 · 数据域 · 接口 · 时效，汇总可复用 / 需申请 / 需新建，盘中类需求自动查时效够不够）、**IT 决策清单**（选项 / 推荐 / 理由 / 影响）→ ADR（含数据地图）；有页面底稿时优先建议「在现页增量改」 | 规则 |
| **出样** | 可点的 HTML 原型：有底稿且调模型时，模型读 HTML 复刻布局再按需求改；失败则规则插列；无底稿用四套模板。手机版、客户版可切 | 模型读底稿 + 规则兜底 |
| **对账 · 一本账** | 需求从受理到交付全程一本账（受理 / 已确认 / 开发中 / 已交付 / 已对账）；交付后回写实际人天 → 写入 `data/history_learned.json`，作为后续复盘依据；**需求经营看板**（各部门需求量、类型、状态漏斗、平均追问与澄清轮次、有效模型估算预计省时、估算偏差）全部由台账实时算出；审计台账可导出 CSV | 纯程序计算 |
| 隐盾 | 原话、上传的 HTML 底稿、仓库代码摘录进模型前都先脱敏（手机、账号、证件、内网地址、密钥、客户 / 机构名 → 语义标签，模型输出再还原），本地规则；凭据类文件（.env、*.pem、*secret*）不读；**沙箱策略** `sandbox.yaml` 一页写清所有访问边界并标出代码执行位置；**模型调用审计**每次调用落一行 | 规则 |

估时原则：**模型、规则模板、历史记录各自说明依据，供人工比较，不自动加权混合**。模型独立拆工作并分别估算以前与现在，程序校验和汇总模型逐项数字；规则公式单列为参考，不用来补写模型结果。历史实际工时可能对应不同范围与开发条件，不直接套入本次需求。各处按 8 小时 = 1 人天换算，表示投入工时，不是日历工期；只有成功的模型估时进入模型工时汇总。

### v0.4 新增（借鉴 OryxOS 的企业级 Agent 运行时设计，只取模式不搬代码）

| 能力 | 做什么 | 在哪 |
|---|---|---|
| **追问记忆**（问过的不再问） | 同一提出方（部门·角色）上次答过的口径记住，下次同类需求直接沿用并标 🧠，业务改口径记忆随之更新；只记答复口径，不记原话与客户信息 | `xuzhi/memory.py`；问清清单、确认消息、需求单都带沿用标记；一本账页可看可清；企微 `/记忆` |
| **模型调用审计** | 每次大模型调用记：用途、渠道、网关主机、模型、耗时、出站脱敏标签数、出站明文敏感项数（应为 0）、成败；不记正文 | `xuzhi/audit.py`；一本账页「模型调用审计」可导出 CSV；`GET /api/v1/audit/llm` |
| **沙箱策略一页** | 散在各处的白名单收成 `sandbox.yaml`：模型协议、仓库协议与内网拒绝、凭据文件、问码工具白名单、接口监听与 Token、提醒开关…每条标执行位置；改文件即改行为 | `xuzhi/sandbox.py`；隐盾页展示；`GET /api/v1/sandbox`；MCP 资源 `xuzhi://sandbox` |
| **REST API + MCP** | 任何入口都能接：工单系统、公司 AI 平台里的智能体、Cursor / Claude Code 都能调问清 / 估量 / 定架 / 写单；同样进一本账 | `api.py`、`mcp_server.py`、`xuzhi/service.py` |
| 催办摘要（附加，默认不推送） | 从一本账算出待确认超期 / 已交付未对账 / 本周受理，只有数量、标题、天数；企微 `/摘要` 拉取，或命令行、接口；定时推送要三把锁同时打开 | `xuzhi/reminders.py` |

## 目录

```
app.py                  Streamlit 界面        run_demo.py  命令行全流程
xuzhi/config.py         配置与品牌            xuzhi/llm.py  OpenAI 兼容客户端 + mock
xuzhi/privacy.py        隐盾                  xuzhi/ledger.py  SQLite 台账（留痕 + 飞轮）
xuzhi/knowledge.py      知识层加载            xuzhi/textutil.py  领域词典与文本工具
xuzhi/pipeline/         intake 问清 · spec 写单 · estimate 模型估量 · estimate_references 规则与历史参考 · tasks 双轨任务结构 · architect 定架 · prototype 出样
xuzhi/drafts.py         页面底稿：上传 HTML / URL / git 拉取与解析
xuzhi/memory.py         追问记忆（问过的不再问）      xuzhi/audit.py  模型调用审计
xuzhi/sandbox.py        沙箱策略加载（sandbox.yaml）  xuzhi/reminders.py  催办摘要（附加，默认不推送）
xuzhi/service.py        服务层：跑流水线 + 记账 + 序列化（REST / MCP 共用）
xuzhi/channels/wecom/   企微入口：长连接 · 消息分发 · 答问会话 · 企微 markdown 渲染
api.py                  REST API（FastAPI，/api/v1/*）  mcp_server.py  MCP 服务（xuzhi_clarify / estimate / architect / spec …）
sandbox.yaml            沙箱策略（所有访问边界一页）
knowledge/futures_probes.json   期货追问知识库（36 条示例，可增删）
knowledge/system_catalog.json   公司系统 / 组件目录（11 个虚构系统）
data/history_requirements.json  历史需求库（30 条虚构，含估算与实际人天）
data/samples/           五个样例：微信 / 纪要 / 邮件 / Word；⑤ 是同一提出方的第二条，用来演示追问记忆
data/drafts/            示例页面底稿（净值日报 HTML）
prompts/                模型润色提示词         tests/  pytest（135 项）
tools/demo_seed.py      演示数据：一本账备份 → 清空 → 预置（重置演示数据.command / .bat）
```

## 现场演示（上台前 5 分钟）

1. 先关掉需知，双击 `重置演示数据.command`（Windows：`重置演示数据.bat`）：一本账恢复成预置状态——20 条虚构需求、10 个部门、各状态都有、7 条已对账，催办摘要与追问记忆也有内容。当前数据先整份备份到 `data/backup/<时间>/`。
2. 双击 `启动需知.command`，Chrome / Edge 全屏（1920×1080），首屏就是样例、原话与「需知，开工」；选模型、填 Key、底稿与仓库都收在「⚙️ 模型与材料」折叠栏里。
3. 建议顺序：样例 ① 开工 → 问清前两题（结算时点、比较基准）答「结算后为准，夜盘算下一交易日」「上一交易日结算价，不复权」→ 重算 → 写单 / 估量 / 定架 / 出样（出样切客户版，集中度列消失）→ 一本账看板 → 选样例 ⑤ 开工，回到「问清」看 🧠 沿用上次口径 2 条。
4. 网络不稳或未填 Key 时，仍可生成需求初稿和原型，并在估量页查看规则模板与历史记录。填好模型后点「大模型分析估时」，已有结果可点「重新分析估时」；只更新本条估算和台账，不重复跑整条流程。模型单次调用上限 `LLM_TIMEOUT`（默认 90 秒）；超时或结果不合格时模型栏显示失败，独立的规则与历史参考保留，不冒充模型结果。

预置数据全部虚构：看板数字只用来展示界面，不是试运行成绩。预置不写模型调用审计（那张表只记真实调用，现场点一次润色就会出现）。

## 企微入口（需求直接从企业微信进来）

同事不用打开网页：在企业微信里 **@需知** 把一堆话发过去，需知当场跑完「隐盾 → 问清 → 写单 → 估量 → 定架」，
把**要和业务确认什么 / 大概多少人天 / 哪几处要 IT 拍板**回到聊天里；问题可以直接在聊天里回答，它按答复重算。

- **启动**：Mac 双击 `启动企微入口.command`；手动 `python -m xuzhi.channels.wecom.app`
- **配置**：`.env` 里填 `WECOM_BOT_ID` / `WECOM_BOT_SECRET` / `WECOM_ALLOW_USERS`（说明见 `.env.example`）
- **机器人怎么建**：企微管理后台 → 安全与管理 → 管理工具 → 智能机器人 → 手动创建 → **API 模式** → 连接方式选「**使用长连接**」。
  长连接由本机主动连出去，**内网不需要公网回调地址**，也不用做消息加解密。Secret 只显示一次，丢了在机器人详情页重新生成。
- **依赖**：企微入口用 `requirements-wecom.txt`（比 Web 端多 `websockets` 与 `claude-agent-sdk`），启动脚本会自动装

**聊天里有两种用法：**

**① 需求分析**（默认模式）
  - 发一段需求 → 回需求卡 + 要确认的问题（带编号）+ 模型双轨工时或待估算状态 + IT 决策点
  - 回「`1 按合约`」这样的编号行（可多行）→ 按答复重算，把握度和工时跟着变

**② 问码**：问关于需知这个项目本身的问题，它去翻代码回答（Claude Agent SDK，只读）
  - 单条：`/问 隐盾的脱敏规则在哪实现的` / `/问 加一个导出 PDF 要改哪几个文件`
  - 连着问：`/模式 问码` 切过去，之后直接发问题；`/模式 需求` 切回来；`/模式` 看当前
  - 回答带「依据」，每条指到 `文件:行号`；追问会续上同一个会话，不用重新交代背景
  - 只读锁死：只放行 Read/Grep/Glob，Bash/Write/Edit/Web* 从模型视野整体移除，`cwd` 锁在 Demo 目录，
    不加载本机 `~/.claude` 配置，另有轮数与墙钟双上限
  - 走 Anthropic 格式接口（`ANTHROPIC_BASE_URL` / `ANTHROPIC_API_KEY`），和需求分析用的 OpenAI 兼容口是两套；
    同一个中转站通常两套都能服务

**命令**：`/help` 用法 ｜ `/模式` 看或切模式 ｜ `/whoami` 查自己的 userid（配白名单用）｜ `/reset` 换一个需求 ｜ `/ping` 存活检查 ｜ `/摘要` 一本账催办摘要 ｜ `/记忆` 追问记忆统计（`/记忆 清 <提出方>`、`/记忆 清空`）

企微进来的需求同样进一本账（事件「企微受理」）；聊天里按编号答复后口径进追问记忆，下次同一提出方不再问。

**对着评分表看**：需求本来就产生在聊天里，把入口放到企微等于零学习成本地接进现有工作流（可复制推广性）；
答问在同一个会话里闭环，现场能做真实环境交互演示（附加分）；进模型前先过隐盾、只放行白名单用户、
回复里不回显明文敏感信息（安全与合规）。

**边界**（企微官方限制，已在代码里兜住）：同一机器人同一时间只允许一条长连接，新连接会踢掉旧的；
最多 3 个并发交互；流式回复最长 6 分钟、单条 20480 字节 —— 所以聊天里只给当场要用的结论，
完整需求单 / 可点原型 / 数据地图仍在 Web 端。群聊只支持文本与图文混排。

## 接口（REST API / MCP）：任何入口都能接

**REST**：`python api.py`（或双击 `启动接口.command`），文档在 http://127.0.0.1:8770/docs 。
- `POST /api/v1/analyze` `{text, source?, answers?, req_id?, polish?, include_spec?, include_prototype?}` → 整条链 JSON（卡片、清单、确认消息、双轨估算、数据地图与决策、需求单）；答复重算时带 `answers` + 上次的 `req_id`，一本账在同一条上更新并写追问记忆
- `POST /api/v1/clarify` 只要问清；`GET /api/v1/requirements[/{id}]`、`POST …/{id}/status`、`POST …/{id}/reconcile` 一本账；`GET /api/v1/dashboard` 看板（含记忆与审计统计）；`GET /api/v1/digest` 催办摘要；`GET /api/v1/audit/llm` 模型调用审计；`GET /api/v1/sandbox` 沙箱策略
- 安全：默认只监听 127.0.0.1；对外必须设 `XUZHI_API_HOST` 且配 `XUZHI_API_TOKEN`（否则拒绝启动）；请求体不接受模型 Key，模型只用服务端 .env；`polish` 默认 false（需求初稿走规则；估时以返回的状态为准，无模型时仍返回独立规则与历史参考）

**MCP**：`python mcp_server.py`（stdio）或 `--transport streamable-http --port 8771`（只听本机）。工具：`xuzhi_clarify` 问清 · `xuzhi_estimate` 双轨估算 · `xuzhi_architect` 数据地图与决策 · `xuzhi_spec` 需求单 · `xuzhi_analyze` 整条链 · `xuzhi_ledger` 一本账 · `xuzhi_reconcile` 对账；资源 `xuzhi://sandbox`、`xuzhi://info`。
Claude Code：`claude mcp add xuzhi -- python /path/to/需知/mcp_server.py`；Cursor / Claude Desktop 的 `mcpServers` 里填同样的命令。三个工具带同一个 `req_id` 时落在同一条账上。

依赖：`pip install -r requirements-api.txt`（比 Web 端多 fastapi / uvicorn / mcp / httpx）。

## 催办摘要（附加功能，默认不推送）

一本账里"该动一动"的需求：待业务确认超 3 天、已交付超 5 天未对账、本周各部门受理数。**只有数量、需求标题、等待天数**，不含原话与客户信息。
- 拉取：企微发 `/摘要`；Web 一本账页「催办摘要」；`python -m xuzhi.reminders --print`；`GET /api/v1/digest`
- 推送（默认关）：要同时满足 `sandbox.yaml` `notify.enabled: true`、.env `XUZHI_DIGEST_WEBHOOK=企微群机器人 webhook`；再配 `XUZHI_DIGEST_TIME=09:00` 则企微入口进程每日定时推，或用系统 cron 跑 `python -m xuzhi.reminders --send`
- 阈值天数在 `sandbox.yaml` notify 段改

## 怎么换成公司真实资料

最省事的路：直接改 `tools/templates/需知_知识库_样本数据.xlsx`（Demo 现用的 30 条历史需求 / 36 条追问规则 / 13 个系统（含数据域与接口）已经在里面，替换成真实的即可），或从空白模板 `需知_知识库填写模板.xlsx` 填起（三个 sheet：历史需求 / 追问规则 / 系统目录，黄色区域填、第 3 行是示例），然后
`python tools/import_knowledge.py template 填好的模板.xlsx`（先加 `--dry-run` 预览）。有工单系统导出的话直接 `python tools/import_knowledge.py tickets 导出.xlsx`，列名按常见叫法自动识别、关键词自动抽取、小时自动折算人天，没有实际工时的记录会被跳过。写入前原 JSON 自动备份为 `.bak`。

手工改也行：

1. `knowledge/futures_probes.json`：加 / 改追问规则（触发词正则 → 问题 → 不问会怎样 → 默认假设 → 影响档）。
2. `knowledge/system_catalog.json`：换成公司真实系统目录（能力关键词、负责团队、接入方式；可选 `data_domains` 数据域与 `interfaces` 接口，填了「定架」就有数据地图；xlsx 模板的 G、H 两列对应）。
3. `data/history_requirements.json`：导入工单系统的历史需求与实际工时（含 `ai_assisted / ai_share`），用于复盘和后续校准；不按历史项目总工时直接套用到当前需求。
4. 估量页对照模型工作项与代码证据、规则模板公式和历史实际记录；分别核对适用范围与前提。修改模板只改变规则参考，不覆盖或预设模型分析结果。
5. `xuzhi/textutil.py`：品种、部门、渠道、数据源词典。

## 评测（成绩单怎么来的）

`python tools/eval_req.py` 把 `data/eval/eval_set.json`（20 条虚构需求，覆盖资管 / 研究 / 投资 / 风控 / 合规 / 营业部 / 财务 / 人力 8 类提出方与报表 / 页面 / 提醒 / 接口 / 数据 / 流程 6 类需求，每条配专家期望）跑一遍规则引擎，对照打分：类型识别、标题、数据来源、指标 / 品种抽取、追问覆盖率与误问率、IT 决策覆盖率、时效判断、脱敏漏检、数据地图定位率，成绩单写到 `data/eval/eval_report.md`。`tests/test_eval.py` 把规则分析阈值挂进测试，改规则退化会红。模型估时另外检查响应结构、证据范围、工时汇总和无模型/失败状态；不要求随机模型输出命中旧规则的固定区间。**换成公司真实需求（脱敏后）就是真实成绩单**：按同一格式填 `cases`，`python tools/eval_req.py 我的评测集.json`。

## 数据与合规

所有样例、历史需求、系统目录、原型数据均为虚构。**所有访问边界写在 `sandbox.yaml`**（隐盾页原样展示并标执行位置）。原话与材料（HTML 底稿、仓库代码摘录）进模型前都经隐盾脱敏，每次模型调用记审计（用途 / 网关主机 / 模型 / 耗时 / 出站脱敏标签数 / 明文敏感项数）；不接生产数据库；Git 仓库只在你主动填链接时浅克隆，只放行 http(s)、拒绝本机 / 内网地址，Token 走环境变量不进命令行与 .git/config，凭据类文件不读；原型预览在独立源的沙箱 iframe 里渲染，页面脚本读不到需知本页；每次分析、答复、状态、对账写入 `data/ledger.sqlite3`（追问记忆、模型调用审计同库；`XUZHI_LEDGER_DB` 可改位置），对账回写到 `data/history_learned.json`（均已 gitignore）。

## 二期

需求版本 diff（增删改、工时与架构影响、需重新确认项）、纪要直通车（一段纪要识别多条需求）、评测集 `eval_req.py`。

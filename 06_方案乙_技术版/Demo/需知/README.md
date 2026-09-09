# 需知 · 需求分析智能体（Demo v0.2）

> 一堆话进来，一份能开工的需求出去。
> 团队赛方案乙（技术部视角）的 Demo。微信三句话、一段会议纪要、一封邮件、一页 Word 扔进去，它先出一版需求，然后告诉你：要和业务确认哪几件事、这活多复杂、大概多少人天、哪几处要 IT 拍板，顺手把可点的原型页画出来。

## 启动

- Mac：双击 `启动需知.command`（首次自动建虚拟环境并装依赖），浏览器打开即可。
- 手动：`pip install -r requirements.txt && streamlit run app.py`
- 命令行看全流程：`python run_demo.py 1`（1～4 为四个样例）
- 测试：`pytest -q tests`（10 项）

不配置 key 也能跑（**mock 模式**，规则引擎兜底）。要接公司 AI 平台：复制 `.env.example` 为 `.env`，填 `LLM_API_BASE / LLM_API_KEY / LLM_MODEL`（OpenAI 兼容接口）。key 由使用者自填，代码与仓库不含密钥。

## 六项技能（v0.2 实现前五项，对账为二期）

| 技能 | 做什么 | 引擎 |
|---|---|---|
| **问清** | 抽需求卡片；按「期货 IT 需求追问知识库」+ 通用检查项生成**待确认清单**（影响高 / 中 / 低，附"不问会怎样"与默认假设）；一键生成给业务的确认消息；业务答复后重算 | 规则 + 模型润色 |
| **写单** | **双版需求单**：业务版一页纸（可签字）+ 技术版（用户故事、Given/When/Then 验收、数据字典、接口草案、非功能），技术条目回链原话 | 规则模板 + 模型润色 |
| **估量** | **双轨估算**：六维复杂度 + 类比估算（历史需求库实际人天按相似度加权）→ 传统人天；再把需求拆成任务，每个任务标 **AI 能做 / AI 做人审 / 人必须做** 并乘 AI 协同系数 → AI 协同人天、省时比例、AI 分工表、建议交付方式（技术部主做 vs 业务在 AI 平台自建） | 纯程序计算 |
| **定架** | 技术栈建议、分层架构图、**复用发现**（对照公司系统目录）、**IT 决策清单**（选项 / 推荐 / 理由 / 影响）→ ADR | 规则 |
| **出样** | 可点的 HTML 原型（报表 / 页面 / 提醒 / 公告影响 / 接口四种形态，手机版、客户版可切） | 模板 |
| 隐盾 | 原话进模型前脱敏（手机、账号、证件、内网地址、密钥、客户 / 机构名 → 语义标签），本地规则 | 规则 |

设计原则沿用研伴：**数字由程序算、模型只组织语言**；没有模型也能跑完整条链。

## 目录

```
app.py                  Streamlit 界面        run_demo.py  命令行全流程
xuzhi/config.py         配置与品牌            xuzhi/llm.py  OpenAI 兼容客户端 + mock
xuzhi/privacy.py        隐盾                  xuzhi/ledger.py  SQLite 台账（留痕 + 飞轮）
xuzhi/knowledge.py      知识层加载            xuzhi/textutil.py  领域词典与文本工具
xuzhi/pipeline/         intake 问清 · spec 写单 · estimate 估量 · tasks 双轨任务层 · architect 定架 · prototype 出样
knowledge/futures_probes.json   期货追问知识库（36 条示例，可增删）
knowledge/system_catalog.json   公司系统 / 组件目录（11 个虚构系统）
data/history_requirements.json  历史需求库（30 条虚构，含估算与实际人天）
data/samples/           四个样例：微信 / 纪要 / 邮件 / Word
prompts/                模型润色提示词         tests/  pytest（10 项）
```

## 怎么换成公司真实的（全部在本机完成，不经过任何外部服务）

最省事的路：直接改 `tools/templates/需知_知识库_样本数据.xlsx`（Demo 现用的 30 条历史需求 / 36 条追问规则 / 11 个系统已经在里面，替换成真实的即可），或从空白模板 `需知_知识库填写模板.xlsx` 填起（三个 sheet：历史需求 / 追问规则 / 系统目录，黄色区域填、第 3 行是示例），然后
`python tools/import_knowledge.py template 填好的模板.xlsx`（先加 `--dry-run` 预览）。有工单系统导出的话直接 `python tools/import_knowledge.py tickets 导出.xlsx`，列名按常见叫法自动识别、关键词自动抽取、小时自动折算人天，没有实际工时的记录会被跳过。写入前原 JSON 自动备份为 `.bak`。

手工改也行：

1. `knowledge/futures_probes.json`：加 / 改追问规则（触发词正则 → 问题 → 不问会怎样 → 默认假设 → 影响档）。
2. `knowledge/system_catalog.json`：换成公司真实系统目录（能力关键词、负责团队、接入方式）。
3. `data/history_requirements.json`：导入工单系统的历史需求与实际工时（含 `ai_assisted / ai_share`）——估算立刻变准，AI 轨随之校准。
4. `xuzhi/pipeline/tasks.py`：AI 协同系数表（按任务性质），按公司 AI 平台使用情况调整。
5. `xuzhi/textutil.py`：品种、部门、渠道、数据源词典。

## 数据与合规

所有样例、历史需求、系统目录、原型数据均为虚构。原话进模型前经隐盾脱敏；不接生产数据库、不读代码仓库；每次分析、答复、决策写入 `data/ledger.sqlite3`（已 gitignore）。

## 二期

对账（需求版本 diff：增删改、工时与架构影响、需重新确认项）、纪要直通车（一段纪要识别多条需求）、上线后实际工时回写与估算偏差看板、评测集 `eval_req.py`。

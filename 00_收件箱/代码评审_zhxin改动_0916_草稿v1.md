# 代码评审 · zhxin 9/16 改动（出样按底稿改表、模型实时目录、加列追问）

> 2026-09-21 ｜ Claude 评审 9d95c4b（约 3900 行新增，Cursor 协作）。测试 51/51 通过，方向对、可保留。
> 下面"已修"的四处是安全合规硬伤（评分里安全合规占 15 分），我已在本次提交里直接改掉；"建议 zhxin 顺手改"的可以转给他。

## 已修（本次提交）

1. **材料未脱敏就进模型**（`drafts.py` / `intake.py` / `prototype.py`）。隐盾原来只处理原话；上传的 HTML 底稿全文、仓库 yml / json / properties / 源码摘录是原样拼进模型 payload 的，而"隐盾"页显示的"脱敏 N 处"只算原话。改法：`draft_context_for_llm` 对每段 html / excerpt 过 `Redactor`，模型输出的 HTML 再用 mapping 还原；`.env`、`*.pem`、`*secret*`、`*credential*`、`id_rsa` 等凭据类文件在读仓库时就跳过（`is_secret_path`）。
2. **原型预览 iframe 同源可读主页**（`app.py _iframe`）。`components.html` 的 sandbox 带 `allow-same-origin`，仓库或模型产出的 HTML 里的脚本能读到需知页面（含 Key 输入框）和 cookie。改法：外层仍用 `components.html`，内容再包一层 `sandbox="allow-scripts"`（不给 same-origin）的 iframe，脚本跑在独立源上。
3. **git clone 无协议 / 主机限制，Token 走命令行**（`drafts.py load_from_git`）。`file:///x.git`、`http://127.0.0.1/…`、`169.254.169.254` 都会直接 clone（复现过），可读服务器本地任意仓库；Token 拼进 URL 作为 argv，`ps` 可见，也留在 `.git/config`。改法：`check_clone_url` 只放行 http(s)、拒绝 localhost / 回环 / 内网 / 链路本地（含 DNS 解析后的地址）；`-c protocol.allow=never` 只开 http(s)；Token 通过 `GIT_CONFIG_KEY_0=http.extraHeader` 环境变量以 Authorization 头传给 git（需 git ≥ 2.31），不进命令行、不进 .git/config；`fetch_url` 同样过白名单。
4. **模型返回异常直接红屏**（`intake.py refine_with_llm`、`app.py`）。`extra_questions` 为 null 会 TypeError，impact 返回 "High" 会在 app 里 KeyError，`analyze` 调用点没有兜底。改法：列表判型、impact 归一到 高 / 中 / 低、app 里 `.get(…, "md")`，开工的 `analyze` 包 try / except 显示错误不崩页。
5. 顺手：`llm.py list_openai_models` 的 `Request()` 移进 try（Fable 自填无协议 Base 会崩页）；`_trim_html` 的 head 也按预算截（另存为的页面整站内联 CSS 会整段送模型）；删掉 `prototype.py adapt_draft_with_llm` 第一个 return 之后 88 行不可达的旧实现（Cursor 合并残留）；`app.py` 去掉未用的 `Path`、`summarize` 导入。

## 建议 zhxin 顺手改（未动）

6. `prototype.py` 只改第一个 `<table>`：底稿有导航表时新列加到导航表、数据表不动（复现过）。建议选 `<th>` 最多的表。
7. `prototype.py` 改名子串匹配："净值→累计净值"会把"单位净值"也改了、表头重复（复现过）。先精确匹配，子串匹配要求唯一命中。
8. `drafts.py _repo_clone_url` 丢掉 `/tree/<branch>/sub` 里的分支名，clone 的是默认分支；捕获后加 `--branch`。
9. `prototype.py` 两处用 `hash()` 生成假数据，出样随 `PYTHONHASHSEED` 变；换 `zlib.crc32` 或 `hashlib`。
10. 出样的"原话中的标识 / 默认值"会把纪要里的"记录：Jerry"当成标识（`_named_ids`），建议排除 记录 / 主持 / 参会 这类字段。
11. 死代码：`_ensure_table_columns`、`_demand_blob`、`Draft.keywords`、`merge_model_catalogs` 无调用；`render` 与 `render_source` 九成相同，可改为 `apply_prototype_view(render_source())`。
12. 测试：`test_drafts.py:84、354` 断言的"原页复刻 / 基于底稿"字样代码里已不存在，靠 `or` 兜底通过；`load_from_git`、`list_openai_models`、`refine_with_llm` 的 api 路径、`adapt_draft_with_llm` 成功路径、多表 / 改名场景没有覆盖。

## 本次一并新增（与评审无关，给 zhxin 知会）

- 定架加**数据地图**：`knowledge/system_catalog.json` 每个系统补 `data_domains`（数据域 + 关键词 + 时效）与 `interfaces`（接口 + 类型 + 状态），需求里的每个数据项定位到 系统 · 数据域 · 接口 · 时效，汇总可复用 / 需申请 / 需新建 / 需计算；盘中 / 下单前类需求自动查时效够不够。分层图数据层与 ADR 同步。xlsx 模板加了 G、H 两列，导入工具认。
- 台账升级为**一本账 + 经营看板**：状态流转（受理 → 已确认 → 开发中 → 已交付 → 已对账）、对账回写实际人天到 `data/history_learned.json`（gitignore，与样本库合并加载，估量飞轮）、看板（部门 / 类型 / 状态 / 月份、平均追问、澄清轮次、AI 协同省时、估算偏差）、审计台账导出 CSV。
- 新增测试 10 项（安全 4、数据地图 4、一本账 2），共 61 项通过。

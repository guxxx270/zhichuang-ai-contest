# 观点账本 · 研判追踪与准确率回溯 Agent（Demo v0.1）

> 团队赛主题原型。一句话：**让每一条研判都可追溯、可回溯、可信任。**
> 链路：研报 / 晨会纪要 → 脱敏 → 抽取观点卡 → 人工确认入账 → 到期用行情回溯 → 胜率榜 / 品种专家图谱 → 带胜率的晨报。

## 5 分钟跑起来

```bash
cd 05_我的方案/Demo/观点账本
python3 -m venv .venv && source .venv/bin/activate      # 可选
pip install -r requirements.txt
cp .env.example .env        # 填公司 AI 平台的 LLM_API_BASE / LLM_API_KEY / LLM_MODEL；不填也能跑（mock 模式）
python run_demo.py --reset  # 命令行跑通整条链
streamlit run app.py        # 打开界面：五个页签 ①抽取 ②账本 ③回溯 ④晨报 ⑤脱敏
```

## 目录

```
app.py                Streamlit 界面
run_demo.py           命令行一键跑通
ledger/
  config.py           读 .env（key 只在这里进入，代码不含任何密钥）
  llm.py              OpenAI 兼容客户端 + mock 模式 + 宽松 JSON 解析
  schema.py           观点卡 / 回溯结果模型，品种词典
  extract.py          抽取：api 模式走 prompts/extract_opinion.md，规则抽取兜底
  privacy.py          脱敏层：正则 + 词典 → <客户_1>/<手机_1> 语义标签，可还原
  store.py            SQLite 账本（去重、溯源）
  market.py           行情：CSV 样例；可选 akshare 拉真实日线
  backtest.py         回溯判定（±1% / 震荡 2%，可配置）
  report.py           胜率榜、品种专家、晨报
prompts/extract_opinion.md   可复用 Prompt（个人赛技能赛道成果）
data/sample_reports/         3 份虚构示例材料
data/sample_prices.csv       示例行情（随机游走，仅供演示）
tests/test_pipeline.py       全链路测试：pytest -q
```

## 和评分维度的对应

| 维度 | 本 Demo 的体现 |
|---|---|
| 业务价值 | 研判从"写完就散"到"可回看谁准"；晨报自动附胜率；决策加权有依据 |
| 创新性 | 非结构化文字观点纳入准确率回溯；品种专家图谱 |
| 技术落地 | 纯 Python + SQLite，任意 OpenAI 兼容模型，无 key 也能跑；规则兜底保证稳定 |
| 可复制 | 换一个研究团队只需换材料与品种词典 |
| 安全合规 | 进模型前脱敏（⑤）、数据不出域、人工确认入账、判定口径可追溯 |

## 下一步（见仓库 README 里程碑）

- 接入公司 AI 平台后，用真实研报做 50 条人工标注评测集，报告抽取准确率。
- 行情换成真实日线（akshare 或公司行情库导出 CSV）。
- 观点卡增加"目标价 / 止损"字段，回溯支持触及目标判定。
- 晨报接入公司 IM 推送。

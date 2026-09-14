# 需知 · 代码评审：zhxin 9/10～9/11 四次提交（草稿 v1）

> 2026-09-14 ｜ Claude 审阅。结论先说：**改得不错，16 项测试全过、界面能跑；两处合规问题已顺手修，其余是建议。** 可以直接转给 zhxin。

## 一、改了什么（按提交）

| 提交 | 内容 | 评价 |
|---|---|---|
| 43f10fd | 模型改为页面选择、Key 只留会话；开工默认规则引擎；加 Qoder Cloud Agents 客户端；Windows 启动 .bat；`conftest.py` 测试强制 mock | 方向对：key 不落盘、默认不调模型；conftest 强制 mock 是好习惯 |
| a3f1797 | 出样：对标指数全表同一日涨跌；`find_added_columns` 抽"加一列 XX"进指标；润色完成提示 | 合理，修了原型把"加一列"抽成两列的毛病 |
| 992274b | 自动润色与手动润色互斥，避免调两次 | 合理 |
| 27ed467 | 浏览器口述（Web Speech API 自定义组件）+ `asr.py` 同音纠错 + 测试 | 功能好用，但有两处要注意（见二） |

## 二、已顺手修（本次 commit）

1. **口述的合规提示**：浏览器 Web Speech API 会把语音送到浏览器厂商的语音服务（Chrome→Google、Edge→Microsoft），这一步发生在隐盾脱敏之前，隐盾管不到。界面和 README 已加醒目提示："含客户姓名、账号的内容请勿口述；正式环境应接公司语音服务或关闭口述"。方案文档里也要这么写，评委问"数据不出公网"时别被这个功能打脸。
2. **Key 免重复输入**：页面 Key 框在本机 `.env` 配了同一网关时自动带出（仍是密码框、仍不进仓库），预设也默认选中 `.env` 里的模型——演示当天不用每次手敲。

## 三、建议 zhxin 看一下的（未动）

1. `xuzhi/asr.py` 的 `transcribe_audio()`（服务端语音识别，硅基流动 SenseVoice）目前没人调用，只用了 `correct_domain_speech`。要么接一个"上传录音识别"入口（用页面已选的硅基流动 key），要么删掉，别留死代码给评委看。
2. `speech_frontend/index.html` 里 `readParentRaw()` 靠 placeholder 含"净值日报"、aria-label 含"原话"去 `window.parent.document` 里找文本框——耦合到文案，改一个 placeholder 就断。建议改成把原话框的 key 固定为 `raw_text`（已有 `st-key-raw_text` 兜底，但当前 key 是 `raw_text_{rev}`，前缀匹配 `[class*="st-key-raw_text"]` 能命中，可保留，但把 placeholder 判断去掉更稳）。
3. `qoder_cloud.py`：`chat()` 轮询最长 180 秒，期间整个页面转圈；`ensure_environment()` 在账号没环境时会自动 `POST /environments` 建一个 "default"——对别人的 Qoder 账号有副作用。建议：超时降到 60～90 秒并在界面提示；不自动建环境，改为报错让用户在控制台建。另外 Qoder Cloud 也是公网服务，合规口径与硅基流动相同。
4. `.env.example` 全注释掉了，但 `检查接入.command` / `run_demo.py` / 稳定性测试脚本仍靠 `.env` 走 api 模式——README 里最好保留一句"命令行工具用 .env"。
5. 小问题：`app.py` 已到 30 KB，模型选择 / Key 表单 / 口述这三块可以拆成 `xuzhi/ui_model.py`、`xuzhi/ui_voice.py`，主文件只留流程；不急。

## 四、约定

- Claude 以后不整包覆盖需知目录，只做单文件改动，改前先看 `git log`；zhxin 改动前也请先 `git pull`。
- 合规底线共同遵守：任何新接入的外部服务（语音、模型、云 Agent）都在界面上写明"数据去哪了"，Demo 只用虚构数据。

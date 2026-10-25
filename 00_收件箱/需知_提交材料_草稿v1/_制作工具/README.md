# 需知 · 提交材料制作工具（草稿 v1）

上一级目录里的 PPT、Demo 视频、方案报告 / 工具说明的 Word 与 PDF，都由这里的脚本生成。改了文稿、换了模型、补完【待团队填写】之后，按下面的命令重出即可。

| 目录 | 内容 |
|---|---|
| `content/PPT内容.json` | PPT 文案（每页标题、要点、配图、讲稿备注） |
| `shots/` | 21 张 Demo 截图 S01～S21（PPT 与 Word 共用） |
| `deck/` | `build.js` 出 PPT，`md2docx.js` 出 Word，`render.sh` 转 PDF 和逐页预览图，`figs.html` 是两张配图 F01/F02 的源文件 |
| `video/` | `final_run.sh` 自动操作需知页面录屏，`narration.json` 旁白，`tts.py` 合成语音，`assemble.py` 合成成片与字幕 |
| `scripts/` | `build_docs.py` 一键出 Word + PDF，`make_script_md.py` 出旁白稿，`package_code.py` 打包源码，`facts.py` 统计代码与测试数字，`pack_submission.py` 打提交包 |

## 一次性准备

录制和合成会产生几百 MB 中间文件，先把整个 `_制作工具` 拷到 SynologyDrive 以外再跑：

```bash
cp -R _制作工具 ~/xuzhi_media && cd ~/xuzhi_media
export XUZHI_APP="<仓库>/06_方案乙_技术版/Demo/需知"     # 需知目录（里面要有 .venv）
python3 -m venv .venv && .venv/bin/pip install -r requirements-tools.txt
(cd deck && npm install)
brew install --cask libreoffice && brew install poppler   # 转 PDF、出预览图
```

- 录屏用本机 Chrome（Playwright `channel="chrome"`），不需要 `playwright install`。
- 旁白用 macOS 自带的「婷婷」语音（`say -v Tingting`）。

## 重出 PPT

```bash
node deck/build.js content/PPT内容.json shots out/需知_方案PPT.pptx
bash deck/render.sh out/需知_方案PPT.pptx out/ppt 80     # 出 PDF + 逐页 PNG，用来检查版式
```

字体写的是微软雅黑；Mac 预览时 `render.sh` 会换成苹方，Windows 的 PowerPoint 直接用微软雅黑。

## 重出 Word / PDF

```bash
.venv/bin/python scripts/build_docs.py ../03_需知_方案报告_草稿v1.md out/需知_方案报告.docx "需知 · 需求分析智能体 —— 团队赛方案报告" --pdf
.venv/bin/python scripts/build_docs.py ../04_需知_工具说明_草稿v1.md out/需知_工具说明.docx "需知 · 工具说明（使用手册与部署指南）" --pdf
```

LibreOffice 转 PDF 时不刷新目录，所以 `--pdf` 会先转一次，按 PDF 页脚把各标题所在页写进 Word 目录，再转一次：PDF 和 Word 的目录都带页码。Word 打开时仍会提示更新域，点「是」按 Word 自己的排版刷新。

## 重录 Demo 视频

视频里的「问清」「估量」两页会显示模型 ID。要换成公司 AI 平台的模型，先改需知目录 `.env` 里的网关和模型，再按顺序跑：

```bash
.venv/bin/python video/tts.py video/narration.json video/tts      # 旁白语音 + 时间轴（录制按它控制每段时长）
.venv/bin/python video/record_cards.py                             # 片头、痛点、片尾三张卡片
bash video/final_run.sh --model record                             # 自动操作页面录 s02～s12，约 15 分钟
.venv/bin/python video/assemble.py video/narration.json video/tts video/clips out/需知_Demo视频.mp4
.venv/bin/python scripts/make_script_md.py video/narration.json video/tts/timeline.json video/clips out/旁白稿.md out/需知_Demo视频.mp4
```

- `final_run.sh` 用 `work/` 下的临时一本账（先灌演示数据），不碰正式数据；需知读自己的 `.env`，脚本不读取也不打印 Key。
- 等模型的时段录制时自动暂停，成片里不会出现空等；录完会把模型来源切到没 Key 的预设，后面几段只跑规则。
- 不带 `--model` 就是纯规则演示（不调模型，页面上没有模型 ID）。
- 成片 1920×1080、30fps、H.264 + AAC，字幕烧录在画面里，另出同名 `.srt`。

## 重拍截图

```bash
bash video/final_run.sh --model capture                           # S01～S20
.venv/bin/python video/capture_s19.py http://127.0.0.1:8713/ shots  # 单补 S19（需先在 8713 端口起一个需知）
```

S21 是 REST 文档页：在需知目录跑 `python api.py`，再 `.venv/bin/python video/shot_api.py http://127.0.0.1:8770/docs shots`。

## 打包源码

```bash
.venv/bin/python scripts/package_code.py <仓库根目录> out/需知_源码.zip
```

只打包 git 跟踪的文件，`.env`、一本账数据库、`history_learned.json` 一律排除（`.env.example` 保留）。

## 打提交包

这一步不用拷出去，直接在 Synology 里的 `_制作工具` 目录下用系统自带的 python3 运行：

```bash
python3 scripts/pack_submission.py .. ../需知_团队赛_提交包_草稿v1.zip
```

把上一级的 PPT（pptx + pdf）、Demo 视频、方案报告与工具说明（docx + pdf）和源码包装进一个 zip。包内文件名去掉「_草稿vN」，源码展开成 `05_需知_源码_v0.6/` 文件夹，另附 `00_材料说明.txt`（内容在 `content/提交包说明.txt`）。字幕、旁白稿、md、提交清单和本目录不进包；同一份材料有多个草稿版本时，取版本号最大的。

打包时会列出 PPT / Word / PDF 里还没补的【待团队…】，以及比 pptx / docx 旧的 PDF（改了源文件忘了重新另存）。补完、另存 PDF 后重跑一次即可。

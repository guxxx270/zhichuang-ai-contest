# 隐盾 · 提交材料制作工具（草稿 v1）

上一级目录里的 PPT、Demo 视频、技术文档的 Word / PDF 和源码包，都由这里的脚本生成。脚本由需知的同名工具改来（品牌、配色、版式和录屏脚本换成隐盾的）。改了文稿、补完【待填写】、或隐盾界面有变化之后，按下面的命令重出。

| 目录 | 内容 |
|---|---|
| `content/PPT内容.json` | PPT 文案：每页标题、要点、配图、讲稿备注（16 页） |
| `shots/` | 截图 S01～S12（整屏，3200×2000）与 PPT 用的局部图 P*（`scripts/crop_shots.py` 从 S* 裁出） |
| `deck/` | `build.js` 出 PPT，`md2docx.js` 出 Word，`render.sh` 转 PDF 和逐页预览图，`figs.html` + `shot_figs.py` 出两张示意图 F01 / F02，`make_bg.py` 出幻灯片背景 |
| `video/` | `narration.json` 旁白，`tts.py` 合成语音与时间轴，`record_cards.py` + `cards.html` 录片头 / 痛点 / 用法 / 评测 / 片尾卡片，`record_app.py` 自动操作隐盾页面录屏，`capture.py` 拍截图，`assemble.py` 合成成片与字幕 |
| `scripts/` | `build_docs.py` 一键出 Word + PDF，`make_script_md.py` 出旁白稿，`package_code.py` 打包源码，`crop_shots.py` 裁 PPT 用局部图，`pack_submission.py` 打提交包 |

## 一次性准备

录制和合成会产生几百 MB 中间文件，先把整个 `_制作工具` 拷到 SynologyDrive 以外再跑：

```bash
cp -R _制作工具 ~/yindun_media && cd ~/yindun_media
python3 -m venv .venv && .venv/bin/pip install -r requirements-tools.txt
(cd deck && npm install)
brew install --cask libreoffice && brew install poppler   # 转 PDF、出预览图
```

- 录屏用本机 Chrome（Playwright `channel="chrome"`），不需要 `playwright install`。
- 旁白用 macOS 自带的「婷婷」语音（`say -v Tingting`）。
- 隐盾本身：在 `06_方案乙_技术版/Demo/隐盾` 下 `streamlit run app.py --server.port 8520 --server.headless true`（或用任一装了 streamlit>=1.50 的 Python）。录屏与截图都需要一个**刚启动的新会话**，录之前重启一次。

## 重拍截图与示意图

```bash
.venv/bin/python video/capture.py http://127.0.0.1:8520/ shots        # S01～S12
(cd deck && ../.venv/bin/python shot_figs.py)                          # 示意图 F01_flow、F02_engine
.venv/bin/python scripts/crop_shots.py shots deck/figs               # P*（PPT 用局部图）与 F*_body（去掉标题行的示意图）
```

## 重出 PPT

```bash
(cd deck && ../.venv/bin/python make_bg.py assets)                     # 背景图，只需一次
node deck/build.js content/PPT内容.json shots out/隐盾_方案PPT.pptx
bash deck/render.sh out/隐盾_方案PPT.pptx out/ppt 80                     # 出 PDF + 逐页 PNG，用来检查版式
```

字体写的是微软雅黑；Mac 预览时 `render.sh` 会换成苹方，Windows 的 PowerPoint 直接用微软雅黑。

## 重出 Word / PDF

```bash
.venv/bin/python scripts/build_docs.py ../03_隐盾_工具说明与技术方案_草稿v1.md out/隐盾_工具说明与技术方案.docx "隐盾 · 工具说明与技术方案" --pdf
```

Word 打开时会提示更新域，点「是」，目录页码才会刷新。

## 重录 Demo 视频

```bash
.venv/bin/python video/tts.py video/narration.json video/tts       # 旁白语音 + 时间轴（录制按它控制每段时长）
.venv/bin/python video/record_cards.py                             # 片头、痛点、三种用法、评测、片尾五张卡片
.venv/bin/python video/record_app.py http://127.0.0.1:8520/        # 自动操作隐盾页面录 s02～s09，约 3 分钟
.venv/bin/python video/assemble.py video/narration.json video/tts video/clips out/隐盾_Demo视频.mp4
.venv/bin/python scripts/make_script_md.py video/narration.json video/tts/timeline.json video/clips out/旁白稿.md out/隐盾_Demo视频.mp4
```

- 隐盾不调模型，录屏全程是真实页面操作；「填入模拟回答」是 `yindun/samples.py` 里手写的一段回答，故意改了几处标签写法，用来演示容错还原。
- 改了旁白文字要先重跑 `tts.py`，再重录卡片和页面（动作按句子时间轴卡点）。
- 成片 1920×1080、30fps、H.264 + AAC，字幕烧录在画面里，另出同名 `.srt`。

## 打包源码

```bash
python3 scripts/package_code.py <仓库根目录> out/隐盾_源码.zip
```

只打包 git 跟踪的文件；`.venv`、缓存、`*.map.json` 映射表一律不在里面。

## 打提交包

这一步不用拷出去，直接在 Synology 里的 `_制作工具` 目录下运行：

```bash
python3 scripts/pack_submission.py .. ../隐盾_个人赛工具赛道_提交包_草稿v1.zip
```

把上一级的 PPT（pptx + pdf）、Demo 视频、技术文档（docx + pdf）和源码包装进一个 zip：文件名去掉「_草稿vN」，源码展开成 `04_隐盾_源码_v0.2/` 文件夹，附 `00_材料说明.txt`（内容在 `content/提交包说明.txt`）。字幕、旁白稿、提交清单和本目录不进包。同一份材料有多个草稿版本时取版本号最大的。

打包时会列出 PPT / Word / PDF 里还没补的【待填写】，以及比 pptx / docx 旧的 PDF（改了源文件忘了重新另存）；补完、另存 PDF 后重跑一次即可。

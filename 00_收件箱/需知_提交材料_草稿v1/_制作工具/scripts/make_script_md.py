"""Demo 视频旁白稿：按成片里每个场景的起止时间排出「时间 · 画面 · 旁白」，供重配人声或现场照念。

用法：python make_script_md.py narration.json tts/timeline.json clips_dir out.md [成片.mp4]
（给了成片就按成片实际时长写表头）
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from imageio_ffmpeg import get_ffmpeg_exe

FFMPEG = get_ffmpeg_exe()
SCENES = {
    "s00_title": "片头：需知 · 需求分析智能体，七个技能标签",
    "s01_pain": "痛点卡：资管运营的微信原话、技术部来回追问，三个痛点依次出现",
    "s02_start": "首页选样例①（微信原话）→ 点「需知，开工」→ 规则初稿秒出，状态框显示模型在后台增强",
    "s03_clarify": "问清：需求要素、待确认清单（🌾 期货口径在前）、右侧确认消息",
    "s04_answer": "在前三题填业务答复 → 「按业务答复重算」→ 顶部「本次变更」卡片",
    "s05_spec": "写单：业务版一页纸 → 切技术版（用户故事、验收标准、数据字典）",
    "s06_estimate": "估量：三路依据并列（独立模型估时 / 规则模板参考 / 历史记录），工作项与依据",
    "s07_arch": "定架：数据地图（可复用 / 需申请 / 需计算）、IT 决策与 ADR 下载",
    "s08_proto": "出样：原型页 → 客户版（集中度列消失）→ 手机版",
    "s09_ledger": "一本账：版本记录（任选两版对比）→ 需求经营看板",
    "s10_memory": "样例⑤（同一提出方的周报需求）→ 问清里 🧠 沿用上次口径",
    "s10b_minutes": "样例⑥（投研晨会纪要）→ 纪要直通车识别 3 条 → 拆成 3 条分别受理",
    "s11_jira": "Jira 需求评估：示例导出 14 条 → 汇总与柱状图 → XZ-1040、XZ-1036 详情",
    "s12_shield": "样例③（邮件夹带客户姓名、资金账号、手机号）→ 隐盾：脱敏 3 处、沙箱策略表",
    "s13_close": "片尾：四个入口、一条流水线、一本账；换三张表即可复制；先问清，再开工",
}


def dur(p: Path) -> float:
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(p)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def mmss(t: float) -> str:
    return f"{int(t // 60)}:{int(t % 60):02d}"


def main() -> None:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    tl = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    clips = Path(sys.argv[3])
    rows, t = [], 0.0
    for sc in cfg["scenes"]:
        sid = sc["id"]
        clip = clips / f"{sid}.mp4"
        if not clip.exists():
            continue
        length = max(dur(clip), tl[sid]["duration"])
        rows.append((mmss(t), mmss(t + length), SCENES.get(sid, sid), "".join(sc["lines"])))
        t += length
    total = dur(Path(sys.argv[5])) if len(sys.argv) > 5 else t
    out = [
        "# 需知 · Demo 视频旁白稿（草稿 v1）",
        "",
        f"> 成片 {mmss(total)}，1920×1080 · 30fps · H.264 + AAC，字幕已烧录（同名 .srt 另附）。旁白为本机合成语音（macOS「婷婷」），"
        "需要真人配音时按下表时间轴照念即可；演示所用需求、系统与数据全部虚构。",
        "> 录制环境：本机启动需知，模型按 `.env` 配置在后台增强；等待模型的时段已剪去。重录方法见提交清单。",
        "",
        "| 时间 | 画面 | 旁白 |",
        "|---|---|---|",
    ]
    for a, b, scene, line in rows:
        out.append(f"| {a}–{b} | {scene} | {line} |")
    Path(sys.argv[4]).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("wrote", sys.argv[4], mmss(t))


if __name__ == "__main__":
    main()

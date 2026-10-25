"""隐盾 Demo 视频旁白稿：按成片里每个场景的起止时间排出「时间 · 画面 · 旁白」，供重配人声或现场照念。

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
    "s00_title": "片头：隐盾 · 贴给大模型前先脱敏；王建国 / 手机号 → <客户_1> / <手机_1>；十类标签",
    "s01_pain": "痛点卡：大赛红线原文 → 一段「直接贴」的原话逐处标红 → 三个难处依次出现",
    "s02_open": "网页首页：高亮「本机运行 · 不联网」→ 点示例「客户来电转研究所」",
    "s03_redact": "脱敏结果：高亮统计（9 处 · 7 类 · 不到 1 毫秒）→ 高亮标签化文本",
    "s04_compare": "切「原文对照」（标红 = 被换掉的内容）→ 高亮可复制的脱敏文本（开头自带保留标签提示）",
    "s05_restore": "切到 ② 还原 → 填入模拟回答（标签被改写）→ 点还原：还原 9 处、纠正写法 4 处，青色为换回的原文",
    "s06_round2": "回到 ①，点「同一客户第二轮追问」→ <客户_1>、<手机_1> 沿用，新账户编成 <账号_2>，会话映射 9 → 10 条",
    "s07_ops": "新会话 → 示例「系统告警排障」：内网地址、连接串口令、api_key、GitHub 令牌换成标签，键名保留",
    "s08_suspect": "新会话 → 贴一段带裸 8 位数字的原话 → 疑似遗漏提示 → 一键确认后一起脱敏",
    "s09_sidebar": "侧边栏：自定义名单输入「陈晓东」→ 类型开关 → 映射表导出",
    "s10_ways": "用法卡：网页版 / 剪贴板一键 / 命令行 · 嵌入（真实命令输出）",
    "s11_eval": "评测卡：开发集检出 100%、误改 0/16、留出集 96%、往返与容错还原 100%、万字约 4 ms、37 项测试；已知边界",
    "s12_close": "片尾：隐盾 · 本地运行 · 不联网 · 即插即用",
}


VOICES = {
    "zh-CN-XiaoxiaoNeural": "微软神经语音「晓晓」，edge-tts 合成",
    "zh-CN-YunxiNeural": "微软神经语音「云希」，edge-tts 合成",
    "zh-CN-YunyangNeural": "微软神经语音「云扬」，edge-tts 合成",
    "Tingting": "macOS「婷婷」",
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
    ver = re.search(r"_草稿v(\d+)", Path(sys.argv[4]).name)
    voice = VOICES.get(cfg.get("voice", ""), cfg.get("voice", ""))
    out = [
        f"# 隐盾 · Demo 视频旁白稿（草稿 v{ver.group(1) if ver else 1}）",
        "",
        f"> 成片 {mmss(total)}，1920×1080 · 30fps · H.264 + AAC，字幕已烧录（同名 .srt 另附）。旁白为合成语音（{voice}），"
        "需要真人配音时按下表时间轴照念即可；演示数据全部虚构（yindun/samples.py）。",
        "> 录制环境：本机启动隐盾 v0.2，Playwright 驱动本机 Chrome 按旁白时间轴自动操作页面，不调任何模型。「模拟回答」是手写的一段回答，故意改了几处标签写法，用来演示容错还原。重录方法见 `_制作工具/README.md`。",
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

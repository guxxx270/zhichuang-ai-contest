"""合成：场景录屏 clips/{sid}.mp4 + 旁白 tts/{sid}.wav + 字幕 → 成片 MP4（1920×1080，H.264 + AAC，字幕烧录）+ SRT。

用法：python assemble.py narration.json tts clips out.mp4
场景时长 = max(录屏, 旁白)；录屏短则定格最后一帧补齐。场景之间 0.18 秒压暗过渡。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from recorder import FFMPEG

FONT_SRC = "/System/Library/AssetsV2/com_apple_MobileAsset_Font8/86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc"
FADE = 0.18
MAX_CHARS = 24


def probe_duration(path: Path) -> float:
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def chunks(text: str) -> list[str]:
    """切成不超过 MAX_CHARS 的字幕块（中文无空格，libass 不会自动换行）：句号、分号处必断，句内按逗号凑满一行；块尾标点去掉。"""
    out = []
    for sent in re.split(r"(?<=[。；！？])", text):
        cur = ""
        for p in re.split(r"(?<=[，、：])", sent):
            if not p:
                continue
            if len(cur) + len(p) <= MAX_CHARS:
                cur += p
            else:
                if cur:
                    out.append(cur)
                while len(p) > MAX_CHARS:
                    out.append(p[:MAX_CHARS])
                    p = p[MAX_CHARS:]
                cur = p
        if cur:
            out.append(cur)
    return [c.rstrip("，、；：。 ") or c for c in out if c.strip()]


def timed_chunks(line: dict) -> list[tuple[float, float, str]]:
    """一句旁白 → [(起, 止, 字幕块)]。有逐词时间：每块从它第一个词念出时开始；没有：按字数比例分。"""
    pieces = chunks(line["text"])
    words = line.get("words")
    if words:
        starts, pos = [], 0
        for p in pieces:
            i = line["text"].find(p, pos)
            pos = i + len(p)
            ws = [w for w in words if w[2] <= i]
            starts.append(ws[-1][0] if ws else line["start"])
        starts[0] = line["start"]
        return list(zip(starts, starts[1:] + [line["end"]], pieces))
    out, t = [], line["start"]
    total = sum(len(p) for p in pieces) or 1
    for p in pieces:
        d = (line["end"] - line["start"]) * len(p) / total
        out.append((t, t + d, p))
        t += d
    return out


def ts_ass(t: float) -> str:
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def ts_srt(t: float) -> str:
    h, rem = divmod(max(t, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"


def main() -> None:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    tts = Path(sys.argv[2]).resolve()
    clips = Path(sys.argv[3]).resolve()
    out = Path(sys.argv[4]).resolve()
    work = out.parent / "_assemble"
    work.mkdir(parents=True, exist_ok=True)
    fonts = work / "fonts"
    fonts.mkdir(exist_ok=True)
    if not (fonts / "PingFang.ttc").exists():
        shutil.copy(FONT_SRC, fonts / "PingFang.ttc")
    timeline = json.loads((tts / "timeline.json").read_text(encoding="utf-8"))

    seg_files, events, offset = [], [], 0.0
    for scene in cfg["scenes"]:
        sid = scene["id"]
        clip, wav = clips / f"{sid}.mp4", tts / f"{sid}.wav"
        if not clip.exists():
            print("skip (no clip):", sid)
            continue
        d_v, d_a = probe_duration(clip), timeline[sid]["duration"]
        L = max(d_v, d_a)
        seg = work / f"{sid}.mp4"
        vf = (f"tpad=stop_mode=clone:stop_duration={max(0.0, L - d_v) + 0.05:.3f},trim=duration={L:.3f},"
              f"fade=t=in:st=0:d={FADE},fade=t=out:st={L - FADE:.3f}:d={FADE},scale=out_range=tv,format=yuv420p")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(clip), "-i", str(wav),
                        "-filter_complex", f"[0:v]{vf}[v];[1:a]apad,atrim=duration={L:.3f}[a]",
                        "-map", "[v]", "-map", "[a]", "-r", "30", "-c:v", "libx264", "-preset", "medium", "-crf", "14",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", str(seg)], check=True)
        seg_files.append(seg)
        for line in timeline[sid]["lines"]:
            for a, b, p in timed_chunks(line):
                events.append((offset + a, offset + b, p))
        print(f"{sid}: video {d_v:.1f}s / audio {d_a:.1f}s -> {L:.1f}s @ {offset:.1f}s")
        offset += L

    lst = work / "segments.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in seg_files), encoding="utf-8")
    joined = work / "joined.mp4"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(joined)], check=True)

    ass = work / "subs.ass"
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Sub,PingFang SC,44,&H00FFFFFF,&H00FFFFFF,&H90000000,&H90000000,1,0,0,0,100,100,1,0,3,14,0,2,80,80,46,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ass.write_text(head + "".join(f"Dialogue: 0,{ts_ass(a)},{ts_ass(b)},Sub,,0,0,0,,{t}\n" for a, b, t in events), encoding="utf-8")
    srt = out.with_suffix(".srt")
    srt.write_text("".join(f"{i}\n{ts_srt(a)} --> {ts_srt(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(events, 1)), encoding="utf-8")

    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(joined),
                    "-vf", f"subtitles={ass.name}:fontsdir={fonts.name}",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-color_range", "tv", "-profile:v", "high",
                    "-c:a", "copy", "-movflags", "+faststart", str(out)], check=True, cwd=work)
    print("wrote", out, f"{probe_duration(out):.1f}s", "subs:", len(events))


if __name__ == "__main__":
    main()

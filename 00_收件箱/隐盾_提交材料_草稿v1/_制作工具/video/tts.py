"""旁白：narration.json → 每个场景一段 WAV + 每句的起止时间与逐词时间（timeline.json）。

用法：python tts.py narration.json out_dir
engine：
  edge  微软神经语音（edge-tts，需联网；只把旁白文字发过去，旁白里没有真实数据）。逐词时间来自 WordBoundary，
        字幕与画面动作按词卡点。合成结果按「语音 + 语速 + 文字」缓存在 out_dir/_cache，改哪句只重合成哪句。
  say   本机 macOS `say`（不联网，声音偏机械）；没有逐词时间，按字数比例估。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import wave
from pathlib import Path

from recorder import FFMPEG

GAP = 0.32          # 句间停顿（秒）
LEAD = 0.35         # 场景开头留白
TAIL = 0.6          # 场景结尾留白


def to_wav(src: Path, out_wav: Path) -> float:
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(src), "-ar", "48000", "-ac", "1", str(out_wav)], check=True)
    with wave.open(str(out_wav)) as w:
        return w.getnframes() / w.getframerate()


def say(text: str, voice: str, rate: int, out_wav: Path) -> tuple[float, list]:
    aiff = out_wav.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), text], check=True)
    d = to_wav(aiff, out_wav)
    aiff.unlink(missing_ok=True)
    return d, []


async def _edge(text: str, voice: str, rate: str, mp3: Path) -> list:
    import edge_tts

    words = []
    with mp3.open("wb") as f:
        async for ch in edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary").stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                # offset / duration 单位 100 纳秒
                words.append([ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]])
    return words


def edge(text: str, voice: str, rate: str, out_wav: Path, cache: Path) -> tuple[float, list]:
    key = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:16]
    mp3, meta = cache / f"{key}.mp3", cache / f"{key}.json"
    if not (mp3.exists() and meta.exists()):
        for attempt in range(3):
            try:
                words = asyncio.run(_edge(text, voice, rate, mp3))
                break
            except Exception as e:  # 网络抖动重试
                if attempt == 2:
                    raise
                print("  retry:", e)
        meta.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    words = json.loads(meta.read_text(encoding="utf-8"))
    # 逐词文字 → 在整句里的字符位置，字幕切块和画面卡点用
    pos, out = 0, []
    for a, b, w in words:
        i = text.find(w, pos)
        if i < 0:
            continue
        out.append([round(a, 3), round(b, 3), i])
        pos = i + len(w)
    return to_wav(mp3, out_wav), out


def silence(seconds: float, out_wav: Path) -> None:
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-t", f"{seconds:.3f}", str(out_wav)], check=True)


def main() -> None:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "_cache"
    cache.mkdir(exist_ok=True)
    engine = cfg.get("engine", "say")
    voice = cfg.get("voice", "Tingting")
    rate = cfg.get("rate", 195)
    timeline = {}
    for scene in cfg["scenes"]:
        sid = scene["id"]
        parts, lines, t = [], [], LEAD
        lead = out / f"{sid}_lead.wav"
        silence(LEAD, lead)
        parts.append(lead)
        for i, line in enumerate(scene["lines"]):
            wav = out / f"{sid}_{i:02d}.wav"
            if engine == "edge":
                d, words = edge(line, voice, str(rate), wav, cache)
            else:
                d, words = say(line, voice, int(rate), wav)
            item = {"text": line, "start": round(t, 3), "end": round(t + d, 3)}
            if words:
                # [词起, 词止, 字符位置]，时间相对场景开头
                item["words"] = [[round(t + a, 3), round(t + b, 3), c] for a, b, c in words]
            lines.append(item)
            parts.append(wav)
            t += d
            gap = out / f"{sid}_{i:02d}_gap.wav"
            silence(GAP if i < len(scene["lines"]) - 1 else TAIL, gap)
            parts.append(gap)
            t += GAP if i < len(scene["lines"]) - 1 else TAIL
        lst = out / f"{sid}_list.txt"
        lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
        scene_wav = out / f"{sid}.wav"
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(scene_wav)],
                       check=True, cwd=out)
        for p in parts:
            p.unlink(missing_ok=True)
        lst.unlink(missing_ok=True)
        timeline[sid] = {"duration": round(t, 3), "lines": lines}
        print(f"{sid}: {t:.1f}s, {len(lines)} lines")
    (out / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
    print("total narration:", round(sum(v["duration"] for v in timeline.values()), 1), "s")


if __name__ == "__main__":
    main()

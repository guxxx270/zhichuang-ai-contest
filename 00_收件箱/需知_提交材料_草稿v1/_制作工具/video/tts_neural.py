"""旁白（神经语音）：narration.json → 每个场景一段 WAV + 每句起止时间与逐词时间（timeline.json），格式同 tts.py，assemble.py 直接用。

用法：python tts_neural.py narration.json out_dir [--fit 已有的 timeline.json]

- 用微软 Edge「大声朗读」的神经语音（edge-tts，要联网，只发出旁白文字），断句和语调比 macOS「婷婷」自然；
  声音与语速读 narration.json 的 neural_voice / neural_rate（百分比，0 为默认语速）。
- 逐词时间来自 WordBoundary，字幕按词出现；合成结果按「声音 + 语速 + 文字」缓存在 out_dir/_cache，改哪句只重合成哪句。
- --fit：画面已按旧时间轴录好时用。每句仍从旧时间轴的起点开始、场景时长不变；哪句放不下，只把这一句调快（最多再快 20%）。
  不带 --fit 则按 tts.py 的留白规则重新排时间轴，重录画面时用。
- 全部旁白统一调到约 -16 LUFS，峰值不超过 -1.5 dB。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import edge_tts

from recorder import FFMPEG
from tts import GAP, LEAD, TAIL

RATE = 48000
MIN_GAP = 0.18        # --fit 时离下一句至少留的空（秒）
END_PAD = 0.25        # --fit 时最后一句离场景结尾至少留的空
MAX_SPEEDUP = 20      # --fit 时单句最多再加快的百分比
HEAD, TAIL_KEEP = 0.03, 0.15   # 第一个词前、最后一个词后各留多少
TARGET_LUFS = -16.0
PEAK_CEIL = -1.5


async def _stream(text: str, voice: str, rate: int, mp3: Path) -> list:
    words = []
    with mp3.open("wb") as f:
        async for ch in edge_tts.Communicate(text, voice, rate=f"{rate:+d}%", boundary="WordBoundary").stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":     # offset / duration 单位 100 纳秒
                words.append([ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]])
    return words


def synth(text: str, voice: str, rate: int, out_wav: Path, cache: Path) -> tuple[float, list]:
    """合成一句，裁到第一个词前、最后一个词后；返回 (时长, [[词起, 词止, 字符位置]])，时间相对这句开头。"""
    key = hashlib.sha1(f"{voice}|{rate}|{text}".encode()).hexdigest()[:16]
    mp3, meta = cache / f"{key}.mp3", cache / f"{key}.json"
    if not (mp3.exists() and meta.exists()):
        for attempt in range(4):
            try:
                words = asyncio.run(_stream(text, voice, rate, mp3))
                break
            except Exception as e:  # noqa: BLE001 —— 连接被重置、限流等，等一下再试
                if attempt == 3:
                    raise
                print(f"  重试（{e.__class__.__name__}）")
                time.sleep(2 + 3 * attempt)
        meta.write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")
    words = json.loads(meta.read_text(encoding="utf-8"))
    a = max(0.0, words[0][0] - HEAD) if words else 0.0
    b = words[-1][1] + TAIL_KEEP if words else None
    trim = f"atrim=start={a:.3f}" + (f":end={b:.3f}" if b else "") + ",asetpts=PTS-STARTPTS"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(mp3), "-af", trim, "-ar", str(RATE), "-ac", "1",
                    "-c:a", "pcm_s16le", str(out_wav)], check=True)
    with wave.open(str(out_wav)) as w:
        d = w.getnframes() / w.getframerate()
    # 逐词文字 → 在整句里的字符位置，字幕切块用
    pos, out = 0, []
    for s, e, w in words:
        i = text.find(w, pos)
        if i < 0:
            continue
        out.append([round(s - a, 3), round(min(e - a, d), 3), i])
        pos = i + len(w)
    return d, out


def place(lines: list[tuple[float, Path]], duration: float, out_wav: Path) -> None:
    """每句放到各自起点，其余补静音，写成一段场景 WAV。"""
    buf = bytearray(int(round(duration * RATE)) * 2)
    for start, wav in lines:
        with wave.open(str(wav)) as w:
            pcm = w.readframes(w.getnframes())
        i = int(round(start * RATE)) * 2
        pcm = pcm[: max(0, len(buf) - i)]
        buf[i:i + len(pcm)] = pcm
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(buf))


def loudness(wavs: list[Path]) -> tuple[float, float]:
    """整段旁白的综合响度（LUFS）与峰值（dB）。"""
    args = [a for p in wavs for a in ("-i", str(p))]
    graph = "".join(f"[{i}:a]" for i in range(len(wavs))) + f"concat=n={len(wavs)}:v=0:a=1,ebur128=peak=true"
    err = subprocess.run([FFMPEG, "-hide_banner", "-nostats", *args, "-filter_complex", graph, "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    summary = err[err.rfind("Summary:"):]
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1)), float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary).group(1))


def main() -> None:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2]).resolve()
    cache = out / "_cache"
    cache.mkdir(parents=True, exist_ok=True)
    fit = json.loads(Path(sys.argv[sys.argv.index("--fit") + 1]).read_text(encoding="utf-8")) if "--fit" in sys.argv else None
    voice, base = cfg.get("neural_voice", "zh-CN-XiaoxiaoNeural"), int(cfg.get("neural_rate", 0))
    timeline, scene_wavs = {}, []
    for scene in cfg["scenes"]:
        sid, texts = scene["id"], scene["lines"]
        old = fit[sid] if fit else None
        if old and len(old["lines"]) != len(texts):
            sys.exit(f"{sid}：旁白 {len(texts)} 句，旧时间轴 {len(old['lines'])} 句，对不上，不能 --fit")
        placed, lines, t = [], [], LEAD
        for i, text in enumerate(texts):
            wav = out / f"{sid}_{i:02d}.wav"
            rate = base
            d, words = synth(text, voice, rate, wav, cache)
            if old:
                start = old["lines"][i]["start"]
                room = (old["lines"][i + 1]["start"] - MIN_GAP if i + 1 < len(texts) else old["duration"] - END_PAD) - start
                if d > room:
                    rate = base + min(MAX_SPEEDUP, math.ceil((d / room - 1) * 100) + 2)
                    d, words = synth(text, voice, rate, wav, cache)
                    if d > room:
                        print(f"  ！{sid} 第 {i + 1} 句调快到 {rate:+d}% 仍超出 {d - room:.2f} 秒")
            else:
                start = t
                t += d + (GAP if i < len(texts) - 1 else TAIL)
            placed.append((start, wav))
            # words：[词起, 词止, 字符位置]，时间相对场景开头
            lines.append({"text": text, "start": round(start, 3), "end": round(start + d, 3), "rate": rate,
                          "words": [[round(start + s, 3), round(start + e, 3), c] for s, e, c in words]})
        duration = old["duration"] if old else round(t, 3)
        scene_wav = out / f"{sid}.wav"
        place(placed, duration, scene_wav)
        for _, wav in placed:
            wav.unlink(missing_ok=True)
        scene_wavs.append(scene_wav)
        timeline[sid] = {"duration": duration, "lines": lines}
        print(f"{sid}: {duration:.1f}s, " + "，".join(f"{ln['end'] - ln['start']:.1f}s" + (f"（{ln['rate']:+d}%）" if ln["rate"] != base else "")
                                                  for ln in lines))
    lufs, peak = loudness(scene_wavs)
    gain = min(TARGET_LUFS - lufs, PEAK_CEIL - peak)
    for p in scene_wavs:
        tmp = p.with_name(p.stem + "_gain.wav")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(p), "-af", f"volume={gain:.2f}dB", "-c:a", "pcm_s16le", str(tmp)], check=True)
        tmp.replace(p)
    (out / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"声音 {voice}，语速 {base:+d}%；响度 {lufs:.1f} → {lufs + gain:.1f} LUFS（增益 {gain:+.1f} dB）")
    print("total narration:", round(sum(v["duration"] for v in timeline.values()), 1), "s")


if __name__ == "__main__":
    main()

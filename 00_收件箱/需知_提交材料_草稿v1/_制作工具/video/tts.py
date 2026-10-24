"""旁白：narration.json → 每个场景一段 WAV + 每句的起止时间（timeline.json）。本机 macOS `say`，不联网。

用法：python tts.py narration.json out_dir
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

from recorder import FFMPEG

GAP = 0.32          # 句间停顿（秒）
LEAD = 0.35         # 场景开头留白
TAIL = 0.6          # 场景结尾留白


def say(text: str, voice: str, rate: int, out_wav: Path) -> float:
    aiff = out_wav.with_suffix(".aiff")
    subprocess.run(["say", "-v", voice, "-r", str(rate), "-o", str(aiff), text], check=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(aiff), "-ar", "48000", "-ac", "1", str(out_wav)], check=True)
    aiff.unlink(missing_ok=True)
    with wave.open(str(out_wav)) as w:
        return w.getnframes() / w.getframerate()


def silence(seconds: float, out_wav: Path) -> None:
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                    "-t", f"{seconds:.3f}", str(out_wav)], check=True)


def main() -> None:
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    voice, rate = cfg.get("voice", "Tingting"), int(cfg.get("rate", 195))
    timeline = {}
    for scene in cfg["scenes"]:
        sid = scene["id"]
        parts, lines, t = [], [], LEAD
        lead = out / f"{sid}_lead.wav"
        silence(LEAD, lead)
        parts.append(lead)
        for i, line in enumerate(scene["lines"]):
            wav = out / f"{sid}_{i:02d}.wav"
            d = say(line, voice, rate, wav)
            lines.append({"text": line, "start": round(t, 3), "end": round(t + d, 3)})
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

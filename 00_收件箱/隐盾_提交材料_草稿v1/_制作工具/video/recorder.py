"""Screen recorder for a Playwright page via CDP screencast.

- Captures JPEG frames with timestamps; pause()/resume() cut idle waits out of the timeline.
- A fake cursor + click ripple + element highlight are injected into the top document so the
  recording reads like a real screen capture (headless Chrome draws no cursor).
- encode() turns the frame list into a constant-fps H.264 clip.
"""
from __future__ import annotations

import base64
import math
import subprocess
import threading
import time
from dataclasses import dataclass, field

from imageio_ffmpeg import get_ffmpeg_exe

FFMPEG = get_ffmpeg_exe()   # imageio-ffmpeg 自带的 ffmpeg，免装系统 ffmpeg

OVERLAY_JS = r"""
(() => {
  if (window.__xzrec) return;
  const style = document.createElement('style');
  style.textContent = `
  #xzrec-cursor{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
    transform:translate(-3px,-2px);transition:none;filter:drop-shadow(0 2px 4px rgba(0,0,0,.55));}
  .xzrec-ripple{position:fixed;z-index:2147483646;pointer-events:none;width:14px;height:14px;margin:-7px 0 0 -7px;
    border-radius:50%;border:3px solid rgba(34,211,238,.95);box-shadow:0 0 18px rgba(34,211,238,.8);
    animation:xzrecRipple .65s ease-out forwards;}
  @keyframes xzrecRipple{from{transform:scale(.4);opacity:1}to{transform:scale(4.2);opacity:0}}
  .xzrec-hl{position:fixed;z-index:2147483645;pointer-events:none;border-radius:12px;
    border:3px solid #FBBF24;box-shadow:0 0 0 4px rgba(251,191,36,.18),0 0 28px rgba(251,191,36,.55);
    opacity:0;transition:opacity .35s ease;}
  .xzrec-hl.on{opacity:1}
  .xzrec-tag{position:fixed;z-index:2147483645;pointer-events:none;background:#FBBF24;color:#0B1222;font:700 15px/1.2 -apple-system,"PingFang SC",sans-serif;
    padding:6px 10px;border-radius:8px;opacity:0;transition:opacity .35s ease;box-shadow:0 6px 18px rgba(0,0,0,.35)}
  .xzrec-tag.on{opacity:1}
  `;
  document.head.appendChild(style);
  const c = document.createElement('div');
  c.id = 'xzrec-cursor';
  c.innerHTML = `<svg width="26" height="26" viewBox="0 0 26 26"><path d="M3 2 L3 21 L8.2 16.4 L11.6 24 L15 22.5 L11.7 15 L19 15 Z"
     fill="#FFFFFF" stroke="#0B1222" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
  document.body.appendChild(c);
  window.__xzrec = {
    move(x, y){ c.style.left = x + 'px'; c.style.top = y + 'px'; },
    ripple(x, y){ const r = document.createElement('div'); r.className='xzrec-ripple'; r.style.left=x+'px'; r.style.top=y+'px';
      document.body.appendChild(r); setTimeout(()=>r.remove(), 800); },
    hl(rect, label){
      const h = document.createElement('div'); h.className='xzrec-hl';
      const pad = 6; h.style.left=(rect.x-pad)+'px'; h.style.top=(rect.y-pad)+'px';
      h.style.width=(rect.width+pad*2)+'px'; h.style.height=(rect.height+pad*2)+'px';
      document.body.appendChild(h); requestAnimationFrame(()=>h.classList.add('on'));
      let t = null;
      if (label){ t = document.createElement('div'); t.className='xzrec-tag'; t.textContent = label;
        const top = rect.y - pad - 36; t.style.left=(rect.x-pad)+'px'; t.style.top=(top < 8 ? rect.y+rect.height+pad+8 : top)+'px';
        document.body.appendChild(t); requestAnimationFrame(()=>t.classList.add('on')); }
      return {h, t};
    },
    clearHl(){ document.querySelectorAll('.xzrec-hl,.xzrec-tag').forEach(e=>{e.classList.remove('on'); setTimeout(()=>e.remove(),400);}); },
  };
})();
"""

# Smooth scroll inside Streamlit's main scroll container (falls back to window).
SCROLL_JS = r"""
async ({dy, ms}) => {
  const cands = [...document.querySelectorAll('[data-testid="stMain"], [data-testid="stAppViewContainer"], section.main, .main')];
  let el = cands.find(e => e.scrollHeight > e.clientHeight + 4 && getComputedStyle(e).overflowY !== 'visible');
  const useWin = !el;
  const start = useWin ? window.scrollY : el.scrollTop;
  const max = useWin ? (document.documentElement.scrollHeight - innerHeight) : (el.scrollHeight - el.clientHeight);
  const target = Math.max(0, Math.min(max, start + dy));
  const t0 = performance.now();
  await new Promise(res => {
    const step = (now) => {
      const p = Math.min(1, (now - t0) / ms);
      const e = p < .5 ? 2*p*p : 1 - Math.pow(-2*p + 2, 2)/2;
      const v = start + (target - start) * e;
      if (useWin) window.scrollTo(0, v); else el.scrollTop = v;
      if (p < 1) requestAnimationFrame(step); else res();
    };
    requestAnimationFrame(step);
  });
  return target - start;
}
"""

SCROLL_TO_JS = r"""
async ({sel, offset, ms, nth}) => {
  const all = document.querySelectorAll(sel);
  const node = all[nth < 0 ? all.length + nth : nth];
  if (!node) return null;
  const cands = [...document.querySelectorAll('[data-testid="stMain"], [data-testid="stAppViewContainer"], section.main, .main')];
  let el = cands.find(e => e.scrollHeight > e.clientHeight + 4 && getComputedStyle(e).overflowY !== 'visible');
  const useWin = !el;
  const start = useWin ? window.scrollY : el.scrollTop;
  const top = node.getBoundingClientRect().top;
  const max = useWin ? (document.documentElement.scrollHeight - innerHeight) : (el.scrollHeight - el.clientHeight);
  const target = Math.max(0, Math.min(max, start + top - offset));
  const t0 = performance.now();
  await new Promise(res => {
    const step = (now) => {
      const p = Math.min(1, (now - t0) / ms);
      const e = p < .5 ? 2*p*p : 1 - Math.pow(-2*p + 2, 2)/2;
      const v = start + (target - start) * e;
      if (useWin) window.scrollTo(0, v); else el.scrollTop = v;
      if (p < 1) requestAnimationFrame(step); else res();
    };
    requestAnimationFrame(step);
  });
  return target - start;
}
"""


@dataclass
class Frame:
    t: float          # seconds on the recording timeline (pauses removed)
    data: object      # jpeg bytes, or a Path when spooled to disk

    def read(self) -> bytes:
        return self.data.read_bytes() if hasattr(self.data, "read_bytes") else self.data


@dataclass
class Recorder:
    page: object
    width: int = 1920
    height: int = 1080
    quality: int = 92
    frames: list[Frame] = field(default_factory=list)
    _cdp: object = None
    _running: bool = False
    _paused: bool = True
    _t0: float = 0.0
    _paused_total: float = 0.0
    _pause_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    cursor: tuple[float, float] = (960.0, 600.0)
    spool: object = None          # pathlib.Path: write frames to disk instead of RAM
    _n: int = 0

    def _keep(self, data: bytes) -> object:
        if self.spool is None:
            return data
        self._n += 1
        path = self.spool / f"f{self._n:07d}.jpg"
        path.write_bytes(data)
        return path

    # ---------- timeline ----------
    def now(self) -> float:
        """Recording-timeline seconds (excludes paused spans)."""
        ref = self._pause_at if self._paused else time.monotonic()
        return ref - self._t0 - self._paused_total

    def start(self) -> None:
        self._cdp = self.page.context.new_cdp_session(self.page)
        self._cdp.on("Page.screencastFrame", self._on_frame)
        self._cdp.send("Page.startScreencast", {"format": "jpeg", "quality": self.quality,
                                                 "maxWidth": self.width, "maxHeight": self.height, "everyNthFrame": 1})
        self._t0 = time.monotonic()
        self._paused = False
        self._running = True
        self.install_overlay()

    def install_overlay(self) -> None:
        self.page.evaluate(OVERLAY_JS)
        self.page.evaluate(f"window.__xzrec && window.__xzrec.move({self.cursor[0]}, {self.cursor[1]})")

    def _on_frame(self, params) -> None:
        try:
            self._cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        except Exception:
            pass
        if self._paused or not self._running:
            return
        data = base64.b64decode(params["data"])
        with self._lock:
            self.frames.append(Frame(self.now(), self._keep(data)))

    def pause(self) -> None:
        if not self._paused:
            self._pause_at = time.monotonic()
            self._paused = True

    def resume(self) -> None:
        if self._paused:
            self._paused_total += time.monotonic() - self._pause_at
            self._paused = False
            # Force a fresh frame right away so the cut lands on the current screen.
            self.snap()

    def snap(self) -> None:
        """Grab one frame explicitly (screencast only emits on change)."""
        if self._paused:
            return
        shot = self.page.screenshot(type="jpeg", quality=self.quality)
        with self._lock:
            self.frames.append(Frame(self.now(), self._keep(shot)))

    def stop(self) -> None:
        self.snap()
        self._running = False
        try:
            self._cdp.send("Page.stopScreencast")
        except Exception:
            pass

    def sleep(self, seconds: float) -> None:
        """Wait while letting Playwright dispatch screencast frames (plain time.sleep starves the CDP stream)."""
        if seconds > 0:
            self.page.wait_for_timeout(int(seconds * 1000))

    def hold(self, seconds: float) -> None:
        """Keep recording the static screen for N seconds (re-snap so the tail frame exists)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.sleep(min(0.25, max(0.0, end - time.monotonic())))
        self.snap()

    def wait_until(self, t_target: float) -> None:
        """Wait until the recording timeline reaches t_target (used to sync with narration cues)."""
        while self.now() < t_target:
            self.sleep(0.05)

    # ---------- cursor / effects ----------
    def move_to(self, x: float, y: float, ms: int = 650) -> None:
        x0, y0 = self.cursor
        steps = max(8, int(ms / 16))
        for i in range(1, steps + 1):
            p = i / steps
            e = 1 - math.pow(1 - p, 3)
            xi, yi = x0 + (x - x0) * e, y0 + (y - y0) * e
            self.page.mouse.move(xi, yi)
            self.page.evaluate(f"window.__xzrec && window.__xzrec.move({xi:.1f}, {yi:.1f})")
            self.sleep(ms / 1000 / steps)
        self.cursor = (x, y)

    def click_at(self, x: float, y: float, ms: int = 650, settle: float = 0.25) -> None:
        self.move_to(x, y, ms)
        self.sleep(0.12)
        self.page.evaluate(f"window.__xzrec && window.__xzrec.ripple({x:.1f}, {y:.1f})")
        self.page.mouse.click(x, y)
        self.sleep(settle)

    def click(self, locator, ms: int = 650, settle: float = 0.25, dx: float = 0.5, dy: float = 0.5) -> None:
        locator.scroll_into_view_if_needed(timeout=10000)
        box = locator.bounding_box()
        if not box:
            raise RuntimeError("element has no bounding box")
        x, y = box["x"] + box["width"] * dx, box["y"] + box["height"] * dy
        self.click_at(x, y, ms, settle)

    def type_text(self, locator, text: str, delay_ms: int = 55) -> None:
        self.click(locator)
        locator.press_sequentially(text, delay=delay_ms)

    def highlight(self, locator, label: str = "", seconds: float = 2.0, clear: bool = True) -> None:
        try:   # 高亮只是锦上添花：找不到元素就跳过，不中断录制
            box = locator.bounding_box(timeout=4000)
        except Exception:
            box = None
        if not box:
            return
        self.page.evaluate("([r, l]) => window.__xzrec && window.__xzrec.hl(r, l)",
                           [{"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"]}, label])
        self.sleep(seconds)
        if clear:
            self.page.evaluate("window.__xzrec && window.__xzrec.clearHl()")
            self.sleep(0.35)

    def clear_highlights(self) -> None:
        self.page.evaluate("window.__xzrec && window.__xzrec.clearHl()")

    def scroll(self, dy: float, ms: int = 900) -> float:
        moved = self.page.evaluate(SCROLL_JS, {"dy": dy, "ms": ms})
        self.sleep(0.1)
        return moved or 0.0

    def scroll_to(self, selector: str, offset: float = 90, ms: int = 900, nth: int = 0) -> None:
        self.page.evaluate(SCROLL_TO_JS, {"sel": selector, "offset": offset, "ms": ms, "nth": nth})
        self.sleep(0.1)

    # ---------- encoding ----------
    def encode(self, out_path: str, fps: int = 30, t_from: float = 0.0, t_to: float | None = None,
               min_duration: float = 0.0) -> float:
        """Constant-fps H.264 clip from the captured frames between [t_from, t_to]."""
        with self._lock:
            frames = sorted(self.frames, key=lambda f: f.t)
        if not frames:
            raise RuntimeError("no frames captured")
        end = frames[-1].t if t_to is None else t_to
        dur = max(end - t_from, min_duration)
        n = max(1, int(round(dur * fps)))
        cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg", "-i", "-",
               "-vf", f"scale={self.width}:{self.height}:flags=lanczos:in_range=pc:out_range=tv,format=yuv420p", "-color_range", "tv",
               "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-r", str(fps), "-movflags", "+faststart", out_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        idx = 0
        cur_frame = frames[0]
        cur = cur_frame.read()
        for k in range(n):
            t = t_from + k / fps
            moved = False
            while idx < len(frames) and frames[idx].t <= t:
                cur_frame = frames[idx]
                idx += 1
                moved = True
            if moved:
                cur = cur_frame.read()
            proc.stdin.write(cur)
        proc.stdin.close()
        proc.wait()
        if proc.returncode:
            raise RuntimeError(f"ffmpeg failed for {out_path}")
        return n / fps

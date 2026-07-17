"""
jammsprite.core — the pipeline that turns video/photos into clean ASCII sprites.

Per frame:
    source image / video frame
      -> background removal            (rembg, isnet-general-use model)
      -> silhouette cleanup            (largest blob, hole-fill, edge smoothing,
                                        1px erosion to trim the fuzzy halo)
      -> crop to subject + small pad
      -> fit into a glyph grid          (preserve aspect, margins, feet on floor)
      -> per-cell sampling              (brightness -> ramp char, keep RGB)
      -> SOLID FILL + despeckle         (dark fur never becomes a hole,
                                        floating stray glyphs are dropped)
      -> ASCII frame {chars, colors}

A "frame" everywhere in this module is a dict:
    {"chars":  [str, ...],                 # rows of glyphs, space = empty
     "colors": [[ "r,g,b" | None, ...]]}   # per-cell RGB, None = empty
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy import ndimage

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
VID_EXT = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".gif")

#: Named tint presets for the monochrome renderers (dark -> bright base color).
TINTS = {
    "green": (124, 255, 178),   # classic phosphor
    "amber": (255, 191, 80),    # warm CRT
    "cyan": (110, 235, 255),
    "white": (235, 235, 235),
    "red": (255, 110, 110),
    "matrix": (0, 255, 70),
}


# ------------------------------------------------------------------ config ---
@dataclass
class Config:
    cols: int = 150          # glyph grid width  (detail vs. size)
    rows: int = 84           # glyph grid height
    char_aspect: float = 0.5 # monospace cell width / height
    ramp: str = " .:-=+ox*scaeX#%@&"        # dark -> bright
    pad_x: int = 9           # in-grid margin so tail/paws never clip
    pad_top: int = 5
    pad_bot: int = 4
    alpha_cut: int = 130     # mask threshold after background removal
    median: int = 5          # mask edge smoothing (odd; 0 = off)
    contrast: tuple = (4, 96)  # percentile stretch of subject luminance
    model: str = "isnet-general-use"
    # rembg is fed an image at least this wide for a sharper mask
    rembg_width: int = 700
    fg: tuple = TINTS["green"]
    bg: tuple = (7, 11, 9)

    @property
    def usable_w(self) -> float:
        return (self.cols - 2 * self.pad_x) * self.char_aspect

    @property
    def usable_h(self) -> int:
        return self.rows - self.pad_top - self.pad_bot


def parse_tint(value: str) -> tuple:
    """'amber' or '255,191,80' -> (255, 191, 80). Raises ValueError on junk."""
    v = value.strip().lower()
    if v in TINTS:
        return TINTS[v]
    parts = [p.strip() for p in v.split(",")]
    if len(parts) != 3:
        raise ValueError(
            f"tint must be one of {sorted(TINTS)} or 'R,G,B' — got {value!r}"
        )
    rgb = []
    for p in parts:
        if not p.isdigit() or not 0 <= int(p) <= 255:
            raise ValueError(f"tint channels must be integers 0-255 — got {value!r}")
        rgb.append(int(p))
    return tuple(rgb)


# ------------------------------------------------------------- background ----
_SESSION = {}


def _session(model: str):
    if model not in _SESSION:
        try:
            from rembg import new_session
        except ImportError:
            sys.exit("missing dependency: rembg\n  pip install rembg onnxruntime")
        _SESSION[model] = new_session(model)
    return _SESSION[model]


def clean_mask(alpha: np.ndarray, cfg: Config) -> np.ndarray:
    """Binary subject mask: largest blob, holes filled, edges smoothed/trimmed."""
    m = alpha > cfg.alpha_cut
    if m.sum() == 0:
        return m
    if cfg.median >= 3:
        m = ndimage.median_filter(m.astype(np.uint8), size=cfg.median).astype(bool)
    lbl, n = ndimage.label(m)
    if n > 1:                                   # keep only the biggest object
        sizes = ndimage.sum(np.ones_like(lbl), lbl, range(1, n + 1))
        m = lbl == (int(np.argmax(sizes)) + 1)
    m = ndimage.binary_fill_holes(m)            # no interior gaps
    m = ndimage.binary_closing(m, iterations=2) # smooth nicks
    m = ndimage.binary_erosion(m, iterations=1) # trim fuzzy halo
    return m


# ------------------------------------------------------ frame transformations
def despeckle(chars: list, colors: list) -> None:
    """Drop isolated glyphs with no orthogonal neighbour. Mutates in place.

    `chars` is a list of lists of single characters (not yet joined)."""
    rows, cols = len(chars), len(chars[0])
    for gy in range(rows):
        for gx in range(cols):
            if chars[gy][gx] == " ":
                continue
            if not any(
                0 <= gy + d < rows and 0 <= gx + e < cols and chars[gy + d][gx + e] != " "
                for d, e in ((1, 0), (-1, 0), (0, 1), (0, -1))
            ):
                chars[gy][gx] = " "
                colors[gy][gx] = None


def flip_frame(frame: dict) -> dict:
    """Return a new frame mirrored horizontally (sprite faces the other way)."""
    return {
        "chars": [row[::-1] for row in frame["chars"]],
        "colors": [list(reversed(row)) for row in frame["colors"]],
    }


def frame_to_ascii(img: Image.Image, cfg: Config):
    """Convert one PIL image to an ASCII frame dict, or None if no subject found."""
    if img.width < cfg.rembg_width:
        img = img.resize((cfg.rembg_width, int(img.height * cfg.rembg_width / img.width)))
    from rembg import remove
    rgba = remove(img.convert("RGB"), session=_session(cfg.model))
    rgb = np.asarray(rgba.convert("RGB"), dtype=np.float32)
    mask = clean_mask(np.asarray(rgba.split()[3]), cfg)

    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    pad = 5
    x0, x1 = max(0, xs.min() - pad), min(mask.shape[1], xs.max() + pad)
    y0, y1 = max(0, ys.min() - pad), min(mask.shape[0], ys.max() + pad)
    crop_rgb, crop_m = rgb[y0:y1, x0:x1], mask[y0:y1, x0:x1]

    lum = 0.2126 * crop_rgb[:, :, 0] + 0.7152 * crop_rgb[:, :, 1] + 0.0722 * crop_rgb[:, :, 2]
    lo = np.percentile(lum[crop_m], cfg.contrast[0])
    hi = np.percentile(lum[crop_m], cfg.contrast[1])
    if hi <= lo:
        hi = lo + 1

    bw, bh = x1 - x0, y1 - y0
    scale = min(cfg.usable_w / bw, cfg.usable_h / bh)
    dw = max(1, round(bw * scale / cfg.char_aspect))
    dh = max(1, round(bh * scale))
    norm = np.clip((lum - lo) / (hi - lo), 0, 1).astype(np.float32) * 255
    L = np.asarray(
        Image.fromarray(norm).convert("L").filter(ImageFilter.GaussianBlur(0.5)).resize((dw, dh), Image.LANCZOS)
    )
    M = np.asarray(Image.fromarray((crop_m * 255).astype(np.uint8)).resize((dw, dh), Image.LANCZOS))
    C = np.asarray(Image.fromarray(crop_rgb.astype(np.uint8)).resize((dw, dh), Image.LANCZOS))

    ox, oy = round((cfg.cols - dw) / 2), cfg.rows - cfg.pad_bot - dh
    chars = [[" "] * cfg.cols for _ in range(cfg.rows)]
    colors = [[None] * cfg.cols for _ in range(cfg.rows)]
    ramp = cfg.ramp
    for yy in range(dh):
        for xx in range(dw):
            if M[yy, xx] < 128:
                continue
            idx = 1 + int(round((L[yy, xx] / 255.0) * (len(ramp) - 2)))  # 1.. -> never blank
            gy, gx = oy + yy, ox + xx
            if 0 <= gy < cfg.rows and 0 <= gx < cfg.cols:
                chars[gy][gx] = ramp[idx]
                colors[gy][gx] = f"{int(C[yy, xx, 0])},{int(C[yy, xx, 1])},{int(C[yy, xx, 2])}"

    despeckle(chars, colors)
    return {"chars": ["".join(r) for r in chars], "colors": colors}


# ----------------------------------------------------------- frame sources ---
def frames_from_video(path, start, dur, fps, n):
    tmp = tempfile.mkdtemp()
    cmd = ["ffmpeg", "-loglevel", "error"]
    if start:
        cmd += ["-ss", str(start)]
    if dur:
        cmd += ["-t", str(dur)]
    cmd += ["-i", path, "-vf", f"fps={fps},scale=760:-2", os.path.join(tmp, "f_%04d.png")]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        sys.exit(
            "ffmpeg not found — it is required for video input.\n"
            "  macOS:  brew install ffmpeg\n"
            "  Ubuntu: sudo apt install ffmpeg\n"
            "  Windows: winget install ffmpeg"
        )
    fs = sorted(glob.glob(tmp + "/f_*.png"))
    if not fs:
        sys.exit("ffmpeg produced no frames — check the file / time window")
    if n and len(fs) > n:
        step = len(fs) / n
        fs = [fs[int(i * step)] for i in range(n)]
    return [Image.open(f) for f in fs]


def frames_from_folder(path):
    fs = sorted(f for f in glob.glob(os.path.join(path, "*")) if f.lower().endswith(IMG_EXT))
    if not fs:
        sys.exit(f"no images found in {path}")
    return [Image.open(f) for f in fs]


def frames_from_still(path, breathe):
    base = Image.open(path).convert("RGB")
    if breathe <= 1:
        return [base]
    w, h = base.size
    import math
    out = []
    for i in range(breathe):                      # subtle vertical breathing loop
        s = 1.0 + 0.02 * math.sin(2 * math.pi * i / breathe)
        out.append(base.resize((w, int(h * s))))
    return out


# --------------------------------------------------------------- renderers ---
def _font(size):
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/Library/Fonts/DejaVuSansMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "C:\\Windows\\Fonts\\consola.ttf",
    ):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def render_gif(frames, cfg, path, color=False, duration=120, cw=4, ch=8):
    f = _font(8)
    imgs = []
    for fr in frames:
        W, H = len(fr["chars"][0]), len(fr["chars"])
        img = Image.new("RGB", (W * cw, H * ch), cfg.bg)
        d = ImageDraw.Draw(img)
        for y, row in enumerate(fr["chars"]):
            col = fr["colors"][y]
            for x, c in enumerate(row):
                if c == " ":
                    continue
                fill = cfg.fg
                if color and col and col[x]:
                    fill = tuple(int(v) for v in col[x].split(","))
                d.text((x * cw, y * ch), c, font=f, fill=fill)
        imgs.append(img)
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=duration, loop=0, disposal=2)


def render_sheet(frames, cfg, path, title, cols=5, cw=3, ch=6, color=False):
    small, big = _font(6), _font(16)
    W, H = len(frames[0]["chars"][0]), len(frames[0]["chars"])
    fw, fh = W * cw, H * ch
    rows = (len(frames) + cols - 1) // cols
    pad, top = 6, 30
    sw, sh = cols * fw + (cols + 1) * pad, top + rows * fh + (rows + 1) * pad
    s = Image.new("RGB", (sw, sh), (10, 14, 12))
    d = ImageDraw.Draw(s)
    d.text((pad, 6), f"{title}  -  {len(frames)} frames @ {W}x{H}", font=big, fill=cfg.fg)
    for i, fr in enumerate(frames):
        cx = pad + (i % cols) * (fw + pad)
        cy = top + pad + (i // cols) * (fh + pad)
        d.rectangle([cx - 1, cy - 1, cx + fw, cy + fh], outline=(30, 60, 45))
        for y, row in enumerate(fr["chars"]):
            col = fr["colors"][y]
            for x, c in enumerate(row):
                if c == " ":
                    continue
                fill = cfg.fg
                if color and col and col[x]:
                    fill = tuple(int(v) for v in col[x].split(","))
                d.text((cx + x * cw, cy + y * ch), c, font=small, fill=fill)
    s.save(path)


def frame_text(fr) -> str:
    """One frame as a plain multi-line string (trailing spaces stripped)."""
    return "\n".join(row.rstrip() for row in fr["chars"])


def print_frame(fr):
    """Dump a frame to the terminal (quick sanity check)."""
    print(frame_text(fr))


def play_in_terminal(frames, duration_ms=120, loops=3):
    """Animate frames in the terminal with ANSI cursor control."""
    hide, show, home, clear = "\x1b[?25l", "\x1b[?25h", "\x1b[H", "\x1b[2J"
    sys.stdout.write(hide + clear)
    try:
        for _ in range(max(1, loops)):
            for fr in frames:
                sys.stdout.write(home + frame_text(fr) + "\n")
                sys.stdout.flush()
                time.sleep(duration_ms / 1000.0)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write(show)
        sys.stdout.flush()


# ------------------------------------------------------------------ writers --
def write_json(frames, name, path, flipped_too=False):
    """Write {name: frames} (plus optional pre-mirrored '<name>_flipped') to path."""
    data = {name: frames}
    if flipped_too:
        data[name + "_flipped"] = [flip_frame(f) for f in frames]
    with open(path, "w") as fh:
        json.dump(data, fh)

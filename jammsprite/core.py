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
import shutil
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

#: Dithering modes for the brightness -> glyph quantisation.
DITHERS = ("none", "ordered", "floyd")

# 4x4 Bayer matrix, normalised to 0..1 (threshold offsets for ordered dither)
_BAYER4 = (np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], dtype=np.float32) + 0.5) / 16.0

#: Named tint presets for the monochrome renderers (dark -> bright base color).
TINTS = {
    "green": (124, 255, 178),   # classic phosphor
    "amber": (255, 191, 80),    # warm CRT
    "cyan": (110, 235, 255),
    "white": (235, 235, 235),
    "red": (255, 110, 110),
    "matrix": (0, 255, 70),
    "blue": (90, 160, 255),
    "purple": (200, 140, 255),
    "pink": (255, 140, 200),
    "orange": (255, 150, 60),
    "lime": (170, 255, 80),
    "ice": (200, 235, 255),
}

#: Named glyph-ramp presets (dark -> bright). The FIRST character is a reserved
#: "empty" slot and is never drawn on the subject — the darkest *filled* glyph
#: is the second character. Pick with ``--charset NAME`` (or pass your own
#: ``--ramp`` string). Longer ramps = finer tonal detail; blocky ramps read
#: better at small sizes.
CHARSETS = {
    # the historic default — smooth 17-step, reads well in colour
    "classic": " .:-=+ox*scaeX#%@&",
    # ultra-fine 70-step photographic ramp (Paul Bourke), great for big grids
    "detail": (" .'`^\",:;Il!i><~+_-?][}{1)(|/tfjrxnuvcz"
               "XYUJCLQ0OZmwqpdbkhao*#MW&8%B@$"),
    # Unicode shading blocks — clean, poster-like fills
    "blocks": " ░▒▓█",
    # chunky 6-step, bold at tiny sizes
    "minimal": " .:oO@",
    # retro terminal 10-step
    "retro": " .:-=+*#%@",
    # dot / stipple density
    "dots": " .·•●█",
    # cross-hatch, inky feel
    "hatch": " .-+=xX#▓",
    # pure 0/1 — data-moshy look
    "binary": " 01",
    # thin vertical bars, VU-meter vibe
    "bars": " ▁▂▃▄▅▆▇█",
}


def parse_charset(value: str) -> str:
    """'blocks' -> the preset ramp string; otherwise treat ``value`` as a
    literal ramp. Raises ValueError only if the resolved ramp is too short."""
    v = value.strip()
    ramp = CHARSETS.get(v.lower(), value)
    if len(ramp) < 2:
        raise ValueError(
            f"ramp/charset needs at least 2 characters — got {value!r}. "
            f"Named presets: {sorted(CHARSETS)}"
        )
    return ramp


def invert_ramp(ramp: str) -> str:
    """Flip a ramp dark<->bright while preserving the reserved first slot,
    so subjects render light-on-dark instead of dark-on-light (or vice versa)."""
    if len(ramp) < 2:
        return ramp
    return ramp[:1] + ramp[1:][::-1]


# ------------------------------------------------------------------ config ---
@dataclass
class Config:
    cols: int = 150          # glyph grid width  (detail vs. size)
    rows: int = 84           # glyph grid height
    char_aspect: float = 0.5 # monospace cell width / height
    ramp: str = " .:-=+ox*scaeX#%@&"        # dark -> bright
    gamma: float = 1.0       # tone curve: >1 brightens midtones (denser), <1 deepens
    dither: str = "none"     # "none" | "ordered" (Bayer 4x4) | "floyd" (Floyd-Steinberg)
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


def quantize_levels(t: np.ndarray, mask: np.ndarray, levels: int, dither: str = "none") -> np.ndarray:
    """Map normalised brightness ``t`` (float array, 0..1) to integer levels
    ``0..levels-1``, optionally dithered. Only cells where ``mask`` is True
    matter; others are returned as 0.

    ``dither``: ``"none"`` (plain rounding), ``"ordered"`` (4x4 Bayer
    threshold — crisp, retro, deterministic) or ``"floyd"`` (Floyd-Steinberg
    error diffusion — smooth tonal gradients on short ramps)."""
    if dither not in DITHERS:
        raise ValueError(f"dither must be one of {DITHERS} — got {dither!r}")
    span = max(1, levels - 1)
    t = np.clip(np.asarray(t, dtype=np.float32), 0, 1)
    mask = np.asarray(mask, dtype=bool)
    if dither == "ordered":
        h, w = t.shape
        thr = np.tile(_BAYER4, (h // 4 + 1, w // 4 + 1))[:h, :w]
        v = t * span + (thr - 0.5)
        out = np.clip(np.floor(v + 0.5), 0, span).astype(np.int32)
    elif dither == "floyd":
        h, w = t.shape
        work = t.astype(np.float32).copy() * span
        out = np.zeros((h, w), dtype=np.int32)
        for y in range(h):
            row = work[y]
            nxt = work[y + 1] if y + 1 < h else None
            mrow = mask[y]
            for x in range(w):
                if not mrow[x]:
                    continue
                old = row[x]
                q = int(min(span, max(0, round(old))))
                out[y, x] = q
                err = old - q
                if err == 0.0:
                    continue
                if x + 1 < w:
                    row[x + 1] += err * 0.4375               # 7/16
                if nxt is not None:
                    if x > 0:
                        nxt[x - 1] += err * 0.1875           # 3/16
                    nxt[x] += err * 0.3125                   # 5/16
                    if x + 1 < w:
                        nxt[x + 1] += err * 0.0625           # 1/16
    else:
        out = np.clip(np.floor(t * span + 0.5), 0, span).astype(np.int32)
    out[~mask] = 0
    return out


def dedupe_frames(frames: list) -> list:
    """Drop frames identical to the one before them (and a final frame that
    equals the first, so the loop seam doesn't double up). Returns a new list."""
    out = []
    for fr in frames:
        if out and fr["chars"] == out[-1]["chars"] and fr["colors"] == out[-1]["colors"]:
            continue
        out.append(fr)
    if len(out) > 1 and out[-1]["chars"] == out[0]["chars"] and out[-1]["colors"] == out[0]["colors"]:
        out.pop()
    return out


def trim_frames(frames: list, pad: int = 0) -> list:
    """Crop every frame to the union bounding box of all non-empty cells
    (plus ``pad`` cells of margin). The box is shared across frames so the
    animation never jitters. Returns new frames; empty input returns []."""
    if not frames:
        return []
    rows, cols = len(frames[0]["chars"]), len(frames[0]["chars"][0])
    y0, y1, x0, x1 = rows, -1, cols, -1
    for fr in frames:
        for y, row in enumerate(fr["chars"]):
            s = row.rstrip()
            if not s.strip():
                continue
            lead = len(s) - len(s.lstrip())
            y0, y1 = min(y0, y), max(y1, y)
            x0, x1 = min(x0, lead), max(x1, len(s) - 1)
    if y1 < 0:                                  # all blank — nothing to trim to
        return [dict(chars=list(f["chars"]), colors=[list(r) for r in f["colors"]]) for f in frames]
    y0, y1 = max(0, y0 - pad), min(rows - 1, y1 + pad)
    x0, x1 = max(0, x0 - pad), min(cols - 1, x1 + pad)
    return [
        {
            "chars": [row[x0:x1 + 1] for row in fr["chars"][y0:y1 + 1]],
            "colors": [list(row[x0:x1 + 1]) for row in fr["colors"][y0:y1 + 1]],
        }
        for fr in frames
    ]


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
    return place_on_grid(L, M, C, ox, oy, cfg)


def place_on_grid(L: np.ndarray, M: np.ndarray, C: np.ndarray, ox: int, oy: int, cfg: Config) -> dict:
    """Quantise a downscaled luminance map ``L`` (uint8, dh x dw), mask ``M``
    (uint8, >=128 = subject) and colour map ``C`` (uint8, dh x dw x 3) into a
    ``cols x rows`` glyph grid at offset (ox, oy). Pure numpy, no ML."""
    dh, dw = L.shape
    inside = M >= 128
    t = L.astype(np.float32) / 255.0
    inv_g = 1.0 / cfg.gamma if cfg.gamma and cfg.gamma > 0 else 1.0
    if inv_g != 1.0:
        t = t ** inv_g                               # tone curve for detail control
    ramp = cfg.ramp
    levels = len(ramp) - 1                           # slot 0 is reserved for "empty"
    idx = quantize_levels(t, inside, levels, cfg.dither) + 1   # 1.. -> never blank (SOLID FILL)

    # clip the subject box to the grid
    sy0, sx0 = max(0, -oy), max(0, -ox)
    sy1, sx1 = min(dh, cfg.rows - oy), min(dw, cfg.cols - ox)
    chars = [[" "] * cfg.cols for _ in range(cfg.rows)]
    colors = [[None] * cfg.cols for _ in range(cfg.rows)]
    if sy1 > sy0 and sx1 > sx0:
        ramp_arr = np.array(list(ramp), dtype="<U1")
        sub_in = inside[sy0:sy1, sx0:sx1]
        sub_idx = idx[sy0:sy1, sx0:sx1]
        sub_c = C[sy0:sy1, sx0:sx1].astype(np.int32)
        ys, xs = np.nonzero(sub_in)
        glyphs = ramp_arr[sub_idx[ys, xs]]
        rgb = sub_c[ys, xs]
        gys, gxs = (ys + oy + sy0).tolist(), (xs + ox + sx0).tolist()
        for y, x, g, (r, gg, b) in zip(gys, gxs, glyphs.tolist(), rgb.tolist()):
            chars[y][x] = g
            colors[y][x] = f"{r},{gg},{b}"

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
    fs = [fs[i] for i in even_picks(len(fs), n)]
    imgs = []
    for f in fs:  # eager-load so the temp dir can be removed immediately
        with Image.open(f) as im:
            imgs.append(im.convert("RGB"))
    shutil.rmtree(tmp, ignore_errors=True)
    return imgs


def even_picks(total: int, n: int) -> list:
    """Indices of ``n`` evenly spaced items out of ``total`` (all if n<=0 or total<=n)."""
    if n <= 0 or total <= n:
        return list(range(total))
    step = total / n
    return [min(total - 1, int(i * step)) for i in range(n)]


def frames_from_folder(path, n=0):
    fs = sorted(f for f in glob.glob(os.path.join(path, "*")) if f.lower().endswith(IMG_EXT))
    if not fs:
        sys.exit(f"no images found in {path}")
    fs = [fs[i] for i in even_picks(len(fs), n)]
    out = []
    for f in fs:  # eager-load so no file handles linger
        with Image.open(f) as im:
            out.append(im.convert("RGB"))
    return out


def frames_from_still(path, breathe):
    with Image.open(path) as im:   # eager-load so no file handle lingers
        base = im.convert("RGB")
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


def _runs(row: str, colors, color: bool, fg):
    """Yield (rgb_or_None, text) runs for one row, trailing blanks dropped."""
    row = row.rstrip()
    cur, buf = None, []
    for x, ch in enumerate(row):
        c = None
        if ch != " ":
            if color and colors and colors[x]:
                c = tuple(int(v) for v in colors[x].split(","))
            else:
                c = tuple(fg) if fg else None
        if c != cur and buf:
            yield cur, "".join(buf)
            buf = []
        cur = c
        buf.append(ch)
    if buf:
        yield cur, "".join(buf)


def frame_ansi(fr, color: bool = True, fg=None) -> str:
    """One frame as 24-bit ANSI ('truecolor') text, ready for ``cat``/``echo -e``.

    ``color=True`` uses each cell's own RGB; otherwise every glyph is drawn in
    ``fg`` (an (r,g,b) tuple) — or left uncoloured when ``fg`` is None."""
    out = []
    for y, row in enumerate(fr["chars"]):
        line, open_ = [], False
        for rgb, text in _runs(row, fr["colors"][y], color, fg):
            if rgb is None:
                if open_:
                    line.append("\x1b[0m")
                    open_ = False
                line.append(text)
            else:
                line.append(f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}")
                open_ = True
        if open_:
            line.append("\x1b[0m")
        out.append("".join(line))
    return "\n".join(out)


def write_ansi(frames, path, color=True, fg=None):
    """Write every frame as ANSI truecolor text, separated by a form feed (\\f)."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\f".join(frame_ansi(f, color=color, fg=fg) + "\n" for f in frames))


def print_frame(fr):
    """Dump a frame to the terminal (quick sanity check)."""
    print(frame_text(fr))


def play_in_terminal(frames, duration_ms=120, loops=3, color=False, fg=None):
    """Animate frames in the terminal with ANSI cursor control.

    ``color=True`` plays in per-cell truecolor; otherwise glyphs use ``fg``
    (an (r,g,b) tint) or the terminal's default colour when ``fg`` is None.
    ``loops <= 0`` loops forever (Ctrl-C stops it)."""
    hide, show, home, clear = "\x1b[?25l", "\x1b[?25h", "\x1b[H", "\x1b[2J"
    rendered = [frame_ansi(f, color=color, fg=fg) if (color or fg) else frame_text(f) for f in frames]
    sys.stdout.write(hide + clear)
    try:
        n = 0
        while loops <= 0 or n < loops:
            n += 1
            for txt in rendered:
                sys.stdout.write(home + txt + "\n")
                sys.stdout.flush()
                time.sleep(duration_ms / 1000.0)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\x1b[0m" + show)
        sys.stdout.flush()


# ------------------------------------------------------- html / svg output ---
def _hex(rgb) -> str:
    r, g, b = (int(v) for v in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_svg(frames, cfg, color=False, duration=120, cw=7, ch=13,
               font="ui-monospace,Menlo,Consolas,monospace") -> str:
    """Frames as one standalone SVG string — animated with SMIL when there is
    more than one frame (loops forever; works in every modern browser, scales
    to any size). ``color=True`` keeps per-cell RGB, else glyphs use ``cfg.fg``."""
    W, H = len(frames[0]["chars"][0]), len(frames[0]["chars"])
    n = len(frames)
    total_ms = max(1, duration) * n
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W * cw}" height="{H * ch}" '
        f'viewBox="0 0 {W * cw} {H * ch}" font-family="{font}" font-size="{ch - 1}px">',
        f'<rect width="100%" height="100%" fill="{_hex(cfg.bg)}"/>',
    ]
    for k, fr in enumerate(frames):
        vis = ";".join("1" if i == k else "0" for i in range(n))
        keys = ";".join(f"{i / n:.4f}" for i in range(n))
        g_open = f'<g fill="{_hex(cfg.fg)}"' + (' opacity="0"' if n > 1 else "") + ">"
        out.append(g_open)
        if n > 1:
            out.append(
                f'<animate attributeName="opacity" values="{vis}" keyTimes="{keys}" calcMode="discrete" '
                f'dur="{total_ms}ms" repeatCount="indefinite"/>'
            )
        for y, row in enumerate(fr["chars"]):
            if not row.strip():
                continue
            cells = []
            for rgb, text in _runs(row, fr["colors"][y], color, cfg.fg):
                t = _xml(text)
                if rgb is None or not color:
                    cells.append(t)
                else:
                    cells.append(f'<tspan fill="{_hex(rgb)}">{t}</tspan>')
            text_len = len(row.rstrip())
            out.append(
                f'<text xml:space="preserve" x="0" y="{y * ch + ch - 3}" textLength="{text_len * cw}" '
                f'lengthAdjust="spacingAndGlyphs">{"".join(cells)}</text>'
            )
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)


def render_html(frames, name, cfg, color=False, duration=120) -> str:
    """Frames as one self-contained HTML page: a <pre> plus a few lines of JS
    that step through the embedded frames. Drop it on any static host, or
    copy the <div> for an embed. ``color=True`` keeps per-cell RGB."""
    payload = []
    for fr in frames:
        rows = []
        for y, row in enumerate(fr["chars"]):
            if color:
                rows.append("".join(
                    (f'<span style="color:{_hex(rgb)}">{_xml(t)}</span>' if rgb is not None else _xml(t))
                    for rgb, t in _runs(row, fr["colors"][y], True, None)
                ))
            else:
                rows.append(_xml(row.rstrip()))
        payload.append("\n".join(rows))
    data = json.dumps(payload).replace("</", "<\\/")
    fg, bg = _hex(cfg.fg), _hex(cfg.bg)
    safe = _xml(name)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{safe} - JAMMSPRITE</title>
<style>
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:{bg}}}
.jammsprite{{margin:0;font:12px/13px ui-monospace,Menlo,Consolas,monospace;color:{fg};white-space:pre;background:{bg}}}
</style></head><body>
<div class="jammsprite-embed" data-name="{safe}">
<pre class="jammsprite" id="jammsprite-{safe}"></pre>
<script>
(function(){{
  var frames={data}, i=0, el=document.getElementById("jammsprite-{safe}");
  function show(){{ el.innerHTML=frames[i]; i=(i+1)%frames.length; }}
  show(); if(frames.length>1) setInterval(show,{int(duration)});
}})();
</script>
</div>
</body></html>
"""


# ------------------------------------------------------------------ writers --
def write_json(frames, name, path, flipped_too=False):
    """Write {name: frames} (plus optional pre-mirrored '<name>_flipped') to path."""
    data = {name: frames}
    if flipped_too:
        data[name + "_flipped"] = [flip_frame(f) for f in frames]
    with open(path, "w") as fh:
        json.dump(data, fh)


def read_json(path, anim=None):
    """Load a ``*.frames.json`` and return ``(name, frames)``. Picks ``anim``
    if given, else the first animation in the file. Raises ValueError on junk."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    names = [k for k, v in data.items() if isinstance(v, list) and v]
    if not names:
        raise ValueError(f"no animations found in {path}")
    if anim is None:
        anim = names[0]
    if anim not in data or not isinstance(data[anim], list):
        raise ValueError(f"animation {anim!r} not in {path} — available: {names}")
    frames = data[anim]
    for fr in frames:
        if not isinstance(fr, dict) or "chars" not in fr or "colors" not in fr:
            raise ValueError(f"{path}: frames must be {{chars, colors}} dicts")
    return anim, frames

#!/usr/bin/env python3
"""
asciisprite — turn real video or photos into clean, animated ASCII sprites.

Pipeline (per frame):
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

Outputs (any combination):
    <name>.frames.json   frame data ({chars, colors}) for your own engine
    <name>.gif           animated preview (phosphor green by default)
    <name>.sheet.png     labelled sprite/contact sheet (all frames in a grid)

Usage
-----
    # a short video clip -> a walk animation
    python asciisprite.py walk.mp4 --name walk --start 1.6 --dur 3.0 --frames 16

    # a single still (use a FLAT MID-GRAY or GREEN background, never white)
    python asciisprite.py cat.png --name idle

    # a single still, gently "breathing" (synthesised motion)
    python asciisprite.py cat.png --name sleep --breathe 8

    # a folder of frames, in colour
    python asciisprite.py ./frames_dir --name run --color

Requirements
------------
    pip install rembg onnxruntime pillow scipy numpy
    ffmpeg must be on PATH (only needed for video input)

See README.md for the sourcing tips that make the output actually look good.
"""
from __future__ import annotations
import argparse, glob, json, os, subprocess, sys, tempfile
from dataclasses import dataclass, field

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    from scipy import ndimage
except ImportError as e:  # pragma: no cover
    sys.exit(f"missing dependency: {e}\n  pip install rembg onnxruntime pillow scipy numpy")

IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
VID_EXT = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".gif")


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
    fg_green: tuple = (124, 255, 178)
    bg: tuple = (7, 11, 9)

    @property
    def usable_w(self) -> float:
        return (self.cols - 2 * self.pad_x) * self.char_aspect

    @property
    def usable_h(self) -> int:
        return self.rows - self.pad_top - self.pad_bot


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


def _clean_mask(alpha: "np.ndarray", cfg: Config) -> "np.ndarray":
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


def frame_to_ascii(img: "Image.Image", cfg: Config):
    """Convert one PIL image to an ASCII frame dict: {chars:[str], colors:[[str|None]]}."""
    if img.width < cfg.rembg_width:
        img = img.resize((cfg.rembg_width, int(img.height * cfg.rembg_width / img.width)))
    from rembg import remove
    rgba = remove(img.convert("RGB"), session=_session(cfg.model))
    rgb = np.asarray(rgba.convert("RGB"), dtype=np.float32)
    mask = _clean_mask(np.asarray(rgba.split()[3]), cfg)

    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    pad = 5
    x0, x1 = max(0, xs.min() - pad), min(mask.shape[1], xs.max() + pad)
    y0, y1 = max(0, ys.min() - pad), min(mask.shape[0], ys.max() + pad)
    crop_rgb, crop_m = rgb[y0:y1, x0:x1], mask[y0:y1, x0:x1]

    lum = 0.2126 * crop_rgb[:, :, 0] + 0.7152 * crop_rgb[:, :, 1] + 0.0722 * crop_rgb[:, :, 2]
    lo, hi = np.percentile(lum[crop_m], cfg.contrast[0]), np.percentile(lum[crop_m], cfg.contrast[1])
    if hi <= lo:
        hi = lo + 1

    bw, bh = x1 - x0, y1 - y0
    scale = min(cfg.usable_w / bw, cfg.usable_h / bh)
    dw, dh = max(1, round(bw * scale / cfg.char_aspect)), max(1, round(bh * scale))
    norm = np.clip((lum - lo) / (hi - lo), 0, 1).astype(np.float32) * 255
    L = np.asarray(Image.fromarray(norm).convert("L").filter(ImageFilter.GaussianBlur(0.5)).resize((dw, dh), Image.LANCZOS))
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
                colors[gy][gx] = "%d,%d,%d" % (int(C[yy, xx, 0]), int(C[yy, xx, 1]), int(C[yy, xx, 2]))

    # despeckle: drop isolated glyphs with no orthogonal neighbour
    for gy in range(cfg.rows):
        for gx in range(cfg.cols):
            if chars[gy][gx] == " ":
                continue
            if not any(0 <= gy + d < cfg.rows and 0 <= gx + e < cfg.cols and chars[gy + d][gx + e] != " "
                       for d, e in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                chars[gy][gx] = " "
                colors[gy][gx] = None
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
    subprocess.run(cmd, check=True)
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
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
              "/Library/Fonts/DejaVuSansMono.ttf",
              "/System/Library/Fonts/Menlo.ttc"):
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
                fill = cfg.fg_green
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
    d.text((pad, 6), f"{title}  -  {len(frames)} frames @ {W}x{H}", font=big, fill=cfg.fg_green)
    for i, fr in enumerate(frames):
        cx = pad + (i % cols) * (fw + pad)
        cy = top + pad + (i // cols) * (fh + pad)
        d.rectangle([cx - 1, cy - 1, cx + fw, cy + fh], outline=(30, 60, 45))
        for y, row in enumerate(fr["chars"]):
            col = fr["colors"][y]
            for x, c in enumerate(row):
                if c == " ":
                    continue
                fill = cfg.fg_green
                if color and col and col[x]:
                    fill = tuple(int(v) for v in col[x].split(","))
                d.text((cx + x * cw, cy + y * ch), c, font=small, fill=fill)
    s.save(path)


def print_frame(fr):
    """Dump the first frame to the terminal (quick sanity check)."""
    for row in fr["chars"]:
        print(row.rstrip())


# -------------------------------------------------------------------- main ---
def main(argv=None):
    ap = argparse.ArgumentParser(description="Turn video/photos into clean ASCII sprites.")
    ap.add_argument("input", help="video file, image file, or a folder of image frames")
    ap.add_argument("--name", help="animation name (default: from filename)")
    ap.add_argument("--out", default="out", help="output directory (default: ./out)")
    ap.add_argument("--cols", type=int, default=150)
    ap.add_argument("--rows", type=int, default=84)
    ap.add_argument("--ramp", default=None, help="characters, dark -> bright")
    ap.add_argument("--start", type=float, default=0.0, help="video: window start (s)")
    ap.add_argument("--dur", type=float, default=0.0, help="video: window length (s), 0 = whole clip")
    ap.add_argument("--fps", type=float, default=6.0, help="video: sampling rate")
    ap.add_argument("--frames", type=int, default=16, help="max frames to keep")
    ap.add_argument("--breathe", type=int, default=0, help="still image: synthesise N gently breathing frames")
    ap.add_argument("--color", action="store_true", help="render GIF/sheet in colour (default phosphor green)")
    ap.add_argument("--no-gif", action="store_true")
    ap.add_argument("--no-sheet", action="store_true")
    ap.add_argument("--no-json", action="store_true")
    ap.add_argument("--duration-ms", type=int, default=120, help="GIF frame duration")
    ap.add_argument("--model", default="isnet-general-use", help="rembg model")
    ap.add_argument("--print", action="store_true", help="also print the first frame to stdout")
    a = ap.parse_args(argv)

    cfg = Config(cols=a.cols, rows=a.rows, model=a.model)
    if a.ramp:
        cfg.ramp = a.ramp
    name = a.name or os.path.splitext(os.path.basename(a.input.rstrip("/")))[0]

    # pick a frame source
    if os.path.isdir(a.input):
        srcs = frames_from_folder(a.input)
    elif a.input.lower().endswith(VID_EXT):
        srcs = frames_from_video(a.input, a.start, a.dur, a.fps, a.frames)
    elif a.input.lower().endswith(IMG_EXT):
        srcs = frames_from_still(a.input, a.breathe)
    else:
        sys.exit(f"unrecognised input: {a.input}")

    print(f"[asciisprite] {name}: converting {len(srcs)} frame(s) at {cfg.cols}x{cfg.rows} ...")
    frames = []
    for i, im in enumerate(srcs):
        fr = frame_to_ascii(im, cfg)
        if fr:
            frames.append(fr)
        print(f"  frame {i + 1}/{len(srcs)}", end="\r")
    print()
    if not frames:
        sys.exit("no subject found in any frame — is the background clean / subject in shot?")

    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, name)
    if not a.no_json:
        json.dump({name: frames}, open(base + ".frames.json", "w"))
        print(f"  wrote {base}.frames.json")
    if not a.no_gif:
        render_gif(frames, cfg, base + ".gif", color=a.color, duration=a.duration_ms)
        print(f"  wrote {base}.gif")
    if not a.no_sheet:
        render_sheet(frames, cfg, base + ".sheet.png", name.upper(), color=a.color)
        print(f"  wrote {base}.sheet.png")
    if a.print:
        print_frame(frames[0])
    print(f"[asciisprite] done — {len(frames)} frames.")


if __name__ == "__main__":
    main()

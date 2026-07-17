"""jammsprite command-line interface."""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__

try:
    from . import core
except ImportError as e:  # pragma: no cover
    sys.exit(
        f"missing dependency: {e}\n"
        "  pip install jammsprite  (or: pip install rembg onnxruntime pillow scipy numpy)"
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="jammsprite",
        description="Turn video/photos into clean, animated ASCII sprites.",
    )
    ap.add_argument("input", help="video file, image file, or a folder of image frames")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
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
    ap.add_argument("--color", action="store_true", help="render GIF/sheet in colour (default monochrome tint)")
    ap.add_argument("--tint", default="green",
                    help=f"monochrome tint: {sorted(core.TINTS)} or 'R,G,B' (default: green)")
    ap.add_argument("--flip", action="store_true", help="mirror the sprite horizontally (face the other way)")
    ap.add_argument("--both-ways", action="store_true",
                    help="also write a pre-mirrored '<name>_flipped' animation into the JSON")
    ap.add_argument("--no-gif", action="store_true")
    ap.add_argument("--no-sheet", action="store_true")
    ap.add_argument("--no-json", action="store_true")
    ap.add_argument("--duration-ms", type=int, default=120, help="GIF frame duration")
    ap.add_argument("--model", default="isnet-general-use", help="rembg model")
    ap.add_argument("--print", action="store_true", help="also print the first frame to stdout")
    ap.add_argument("--play", type=int, nargs="?", const=3, default=0, metavar="LOOPS",
                    help="after converting, play the animation in the terminal (default 3 loops)")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)

    cfg = core.Config(cols=a.cols, rows=a.rows, model=a.model)
    if a.ramp:
        cfg.ramp = a.ramp
    try:
        cfg.fg = core.parse_tint(a.tint)
    except ValueError as e:
        sys.exit(str(e))
    name = a.name or os.path.splitext(os.path.basename(a.input.rstrip("/")))[0]

    # pick a frame source
    if os.path.isdir(a.input):
        srcs = core.frames_from_folder(a.input)
    elif a.input.lower().endswith(core.VID_EXT):
        srcs = core.frames_from_video(a.input, a.start, a.dur, a.fps, a.frames)
    elif a.input.lower().endswith(core.IMG_EXT):
        srcs = core.frames_from_still(a.input, a.breathe)
    else:
        sys.exit(f"unrecognised input: {a.input}")

    print(f"[jammsprite] {name}: converting {len(srcs)} frame(s) at {cfg.cols}x{cfg.rows} ...")
    frames = []
    for i, im in enumerate(srcs):
        fr = core.frame_to_ascii(im, cfg)
        if fr:
            frames.append(fr)
        print(f"  frame {i + 1}/{len(srcs)}", end="\r")
    print()
    if not frames:
        sys.exit("no subject found in any frame — is the background clean / subject in shot?")

    if a.flip:
        frames = [core.flip_frame(f) for f in frames]

    os.makedirs(a.out, exist_ok=True)
    base = os.path.join(a.out, name)
    if not a.no_json:
        core.write_json(frames, name, base + ".frames.json", flipped_too=a.both_ways)
        print(f"  wrote {base}.frames.json" + (" (+ flipped)" if a.both_ways else ""))
    if not a.no_gif:
        core.render_gif(frames, cfg, base + ".gif", color=a.color, duration=a.duration_ms)
        print(f"  wrote {base}.gif")
    if not a.no_sheet:
        core.render_sheet(frames, cfg, base + ".sheet.png", name.upper(), color=a.color)
        print(f"  wrote {base}.sheet.png")
    if a.print:
        core.print_frame(frames[0])
    print(f"[jammsprite] done — {len(frames)} frames.")
    if a.play:
        core.play_in_terminal(frames, duration_ms=a.duration_ms, loops=a.play)


if __name__ == "__main__":  # pragma: no cover
    main()

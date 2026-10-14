# Changelog

All notable changes to JAMMSPRITE are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

## [1.3.0] — 2026-10-13

### Added
- **`--dither none|ordered|floyd`** — dithering on the brightness→glyph step.
  `ordered` is a 4×4 Bayer threshold (crisp, deterministic, very retro);
  `floyd` is Floyd–Steinberg error diffusion (smooth tonal gradients even on
  short ramps like `minimal` or `blocks`). Default `none` is pixel-identical
  to 1.2.0 output. Also in the web app (Glyphs → Dither).
- **Truecolor ANSI output.** `--ansi` writes `<name>.ans` — 24-bit ANSI text
  (per-cell RGB with `--color`, else your `--tint`), frames separated by
  form-feed, ready to `cat`. `--play` now plays in colour too (truecolor with
  `--color`, otherwise in the `--tint`). Public API: `frame_ansi()`,
  `write_ansi()`. Web app: **Copy ANSI**.
- **`jammsprite preview FILE.frames.json`** — a terminal player for existing
  frame files. No conversion, no ML model: `--anim`, `--loops`,
  `--duration-ms`, `--color`, `--tint`, `--flip`, `--trim`. Public API:
  `read_json()`.
- **`--html`** — `<name>.html`, one self-contained animated page (a `<pre>`
  plus a few lines of JS, colour or tint) you can host anywhere or paste in as
  an embed. Public API: `render_html()`. Web app: **HTML** export.
- **`--svg`** — `<name>.svg`, a scalable vector render that is SMIL-animated
  when there is more than one frame, with per-cell colour under `--color`.
  Public API: `render_svg()`. Web app: **SVG** export.
- **`--dedupe`** — drop frames identical to the previous one, and a final frame
  that equals the first (loop seam). Public API: `dedupe_frames()`.
- **`--trim [PAD]`** — crop every frame to the subject's *shared* bounding box
  (plus `PAD` cells), so JSON/GIF/sheet get smaller without any jitter. Public
  API: `trim_frames()`. Web app: **Dedupe frames** / **Trim to subject**
  toggles, applied instantly without a re-convert.
- Public API: `quantize_levels()` (the dither-aware quantiser),
  `place_on_grid()` (the pure-numpy grid fill), `even_picks()`, `DITHERS`.

### Changed
- The glyph-grid fill in `frame_to_ascii` is vectorised with numpy
  (`place_on_grid`) — same output as before, noticeably faster per frame.
- `--duration-ms` now also drives the HTML and SVG frame timing.
- `Config` gained a `dither` field (default `"none"`). The frame JSON format
  is **unchanged** — existing `*.frames.json` files and renderers keep working.
- Web app: status errors are prefixed with plain `Error:` text instead of a
  symbol; footer mentions `jammsprite preview`.
- Tests: 30 new cases covering the quantiser (all three modes), grid fill and
  clipping, dedupe, trim, ANSI, HTML, SVG, `read_json`, folder frame limits,
  and the new CLI flags / `preview` subcommand.

### Fixed
- `--frames` was ignored for folder input — every image in the folder was
  converted. It now picks `N` evenly spaced frames like video input does.
- Still-image input left the source file handle open (`Image.open` without a
  context manager); it is now loaded eagerly and closed.

## [1.2.0] — 2026-08-11

### Added
- **JAMMSPRITE Web** (`index.html`) — the whole pipeline as one offline browser
  app, zero install: drop a **photo, animated GIF, or short video** and get a
  sprite. Auto cutout (transparency or flat-background lift) with a
  keep-background mode, **full-color output** or any tint, all glyph styles,
  gamma/invert/flip/breathe, and exports: animated **GIF**, **sprite sheet
  PNG**, single-frame PNG, **`frames.json`** (works with `tools/player.html`
  and game engines), or copy-as-text. Pure JS — includes its own GIF decoder
  and encoder; nothing ever leaves your device.
- **Six new tint presets** in both the CLI and the web app — `blue`, `purple`,
  `pink`, `orange`, `lime`, `ice` — joining `green`, `amber`, `cyan`, `white`,
  `red`, `matrix`. (`--tint` still takes any literal `R,G,B` too.)
- **`--charset NAME`** — pick a named glyph style instead of hand-typing a ramp:
  `classic` (the original), `detail` (70-step photographic ramp for big grids),
  `blocks` (Unicode shading blocks), `minimal`, `retro`, `dots`, `hatch`,
  `binary`, and `bars`. Passing an unknown name falls back to treating it as a
  literal ramp, so `--charset " .oO@"` still works.
- **`--list-charsets`** — print every built-in style with a live preview and
  exit (works without an input file).
- **`--gamma FLOAT`** — tone/detail curve applied to the brightness→glyph
  mapping. `>1` brightens midtones for a denser, more detailed fill; `<1`
  deepens shadows for higher contrast. Default `1.0` (unchanged behaviour).
- **`--invert`** — flip the active ramp dark↔bright for light-on-dark subjects,
  while preserving the reserved "empty" slot so the silhouette stays clean.
- Public API: `CHARSETS`, `parse_charset()`, and `invert_ramp()` are now
  importable from `jammsprite`.

### Changed
- `Config` gained a `gamma` field (default `1.0`). The frame JSON format is
  **unchanged** — existing `*.frames.json` files and renderers keep working.
- Tests: added coverage for charset resolution, ramp inversion, the gamma tone
  curve, and the new CLI flags.

## [1.1.1] — 2026-07-17

### Fixed
- Video input: extracted frames are now loaded eagerly and the temporary
  frame directory is deleted after use (previously it was left behind, and
  PIL kept lazy file handles open).
- Folder input: images are loaded eagerly too — no lingering file handles.

### Changed
- CI no longer runs for commits that only touch docs, examples, or the
  license — saves Actions minutes on gif/README updates.
- `tools/player.html`: removed dead code; README clarifies you need a local
  copy of the player (GitHub renders HTML files as source).
- New example: `examples/ninja.gif` — martial-arts kick cut from free Pexels
  footage, converted with the published pipeline.

## [1.1.0] — 2026-07-15

### Added
- **Proper Python package** — `pip install` it, then run `jammsprite` from anywhere
  (or `python -m jammsprite`). Public API importable as `jammsprite`.
- **`--flip`** — mirror the sprite horizontally (face the other way).
- **`--both-ways`** — write a pre-mirrored `<name>_flipped` animation into the JSON,
  so game engines get both directions for free.
- **`--tint`** — recolor the monochrome renders: named presets
  (`green`, `amber`, `cyan`, `white`, `red`, `matrix`) or any `R,G,B`.
- **`--play [N]`** — play the finished animation right in your terminal (ANSI), N loops.
- **`--version`**.
- **`tools/player.html`** — zero-dependency drag-and-drop player for `*.frames.json`:
  play/pause, fps slider, flip, tint picker, full-colour mode.
- **Tests** (`pytest`) for the pure pipeline math: despeckle, mask cleanup, flips, tint parsing.
- **CI** — GitHub Actions: ruff lint + tests + CLI smoke test on Python 3.9/3.11/3.12.
- Friendly error with install instructions when ffmpeg is missing.
- Windows Consolas added to the font fallback chain for GIF/sheet rendering.

### Changed
- Code reorganised from a single script into the `jammsprite/` package
  (`core.py` pipeline + `cli.py`). The frame JSON format is **unchanged** —
  existing `*.frames.json` files and renderers keep working.
- `asciisprite.py` at the repo root is gone — use the `jammsprite` command.

## [1.0.0] — 2026-07-15

### Added
- Initial release: background removal (rembg), silhouette cleanup, glyph-grid
  fitting, solid fill + despeckle, GIF / sprite-sheet / JSON outputs,
  `--breathe` synthesised motion for stills.

[1.3.0]: https://github.com/JAMMx2/JAMMSPRITE/releases/tag/v1.3.0
[1.2.0]: https://github.com/JAMMx2/JAMMSPRITE/releases/tag/v1.2.0
[1.1.1]: https://github.com/JAMMx2/JAMMSPRITE/releases/tag/v1.1.1
[1.1.0]: https://github.com/JAMMx2/JAMMSPRITE/releases/tag/v1.1.0
[1.0.0]: https://github.com/JAMMx2/JAMMSPRITE/commits/main

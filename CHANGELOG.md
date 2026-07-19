# Changelog

All notable changes to JAMMSPRITE are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow [SemVer](https://semver.org/).

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

[1.1.1]: https://github.com/JAMMx2/JAMMSPRITE/releases/tag/v1.1.1
[1.1.0]: https://github.com/JAMMx2/JAMMSPRITE/releases/tag/v1.1.0
[1.0.0]: https://github.com/JAMMx2/JAMMSPRITE/commits/main

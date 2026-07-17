# Contributing to JAMMSPRITE

PRs welcome — this is a small, sharp tool and the goal is to keep it that way.

## Dev setup

```bash
git clone https://github.com/JAMMx2/JAMMSPRITE.git
cd JAMMSPRITE
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate

# light install (enough for tests/lint — no 170 MB model download):
pip install numpy pillow scipy pytest ruff
pip install -e . --no-deps

# full install (actually run the pipeline):
pip install -e .
```

## Before you open a PR

```bash
ruff check jammsprite tests   # lint — CI enforces this
pytest                        # all tests green
```

Both run automatically in CI on Python 3.9 / 3.11 / 3.12.

## Ground rules

- **Don't break the frame format.** `{chars, colors}` is the public contract —
  game engines and `tools/player.html` depend on it. Additive changes only.
- **Keep the pipeline testable without rembg.** Pure math (masks, grids,
  despeckle, transforms) lives in functions that don't touch the network or
  the ML model, so tests stay fast.
- New CLI flags need: a default that preserves old behaviour, a row in the
  README options table, and a CHANGELOG entry.
- Example GIFs are welcome (see the README tips on footage that cuts cleanly),
  but keep files under ~500 KB each.

## Bugs

Open an issue with the command you ran, your OS/Python version, and — if it's
a cutout-quality problem — the source image/clip if you're able to share it.

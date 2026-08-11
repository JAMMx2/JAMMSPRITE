"""Tests for the pure (no-rembg) parts of the pipeline."""
import json

import numpy as np
import pytest

from jammsprite import core


def make_frame(rows):
    """Tiny frame helper: rows of chars; colors mirror non-space cells."""
    return {
        "chars": list(rows),
        "colors": [[None if c == " " else "1,2,3" for c in row] for row in rows],
    }


# ------------------------------------------------------------------ flips ---
def test_flip_frame_mirrors_rows():
    fr = make_frame(["ab ", "  c"])
    flipped = core.flip_frame(fr)
    assert flipped["chars"] == [" ba", "c  "]
    assert flipped["colors"][0] == [None, "1,2,3", "1,2,3"]


def test_flip_twice_is_identity():
    fr = make_frame(["x. ", " .x", "@@@"])
    assert core.flip_frame(core.flip_frame(fr)) == fr


def test_flip_does_not_mutate_original():
    fr = make_frame(["ab"])
    core.flip_frame(fr)
    assert fr["chars"] == ["ab"]


# -------------------------------------------------------------- despeckle ---
def test_despeckle_drops_isolated_glyph():
    chars = [list("x  "), list("   "), list("  x")]
    colors = [["1,2,3", None, None], [None] * 3, [None, None, "1,2,3"]]
    core.despeckle(chars, colors)
    assert all(c == " " for row in chars for c in row)
    assert all(c is None for row in colors for c in row)


def test_despeckle_keeps_connected_glyphs():
    chars = [list("xx "), list(" x "), list("   ")]
    colors = [["1,2,3", "1,2,3", None], [None, "1,2,3", None], [None] * 3]
    core.despeckle(chars, colors)
    assert chars[0][:2] == ["x", "x"] and chars[1][1] == "x"


def test_despeckle_diagonal_only_is_dropped():
    chars = [list("x "), list(" x")]
    colors = [["1,2,3", None], [None, "1,2,3"]]
    core.despeckle(chars, colors)
    assert all(c == " " for row in chars for c in row)


# -------------------------------------------------------------- clean_mask ---
def test_clean_mask_keeps_largest_blob_and_fills_holes():
    cfg = core.Config(median=0)
    alpha = np.zeros((40, 40), dtype=np.uint8)
    alpha[5:25, 5:25] = 255      # big blob
    alpha[10:14, 10:14] = 0      # hole inside it
    alpha[32:35, 32:35] = 255    # small stray blob
    m = core.clean_mask(alpha, cfg)
    assert m[12, 12]             # hole filled
    assert not m[33, 33]         # stray blob dropped
    assert m.sum() > 200


def test_clean_mask_empty_alpha():
    cfg = core.Config()
    m = core.clean_mask(np.zeros((10, 10), dtype=np.uint8), cfg)
    assert m.sum() == 0


# ------------------------------------------------------------------- tint ---
def test_parse_tint_named():
    assert core.parse_tint("amber") == core.TINTS["amber"]
    assert core.parse_tint("GREEN") == core.TINTS["green"]


def test_parse_tint_rgb_triplet():
    assert core.parse_tint("255, 176, 0") == (255, 176, 0)


@pytest.mark.parametrize("bad", ["", "purple-ish", "1,2", "256,0,0", "1,2,x"])
def test_parse_tint_rejects_junk(bad):
    with pytest.raises(ValueError):
        core.parse_tint(bad)


# ----------------------------------------------------------------- config ---
def test_config_usable_area():
    cfg = core.Config(cols=100, rows=50)
    assert cfg.usable_w == (100 - 2 * cfg.pad_x) * cfg.char_aspect
    assert cfg.usable_h == 50 - cfg.pad_top - cfg.pad_bot


# ------------------------------------------------------------- frame_text ---
def test_frame_text_strips_trailing_space():
    fr = make_frame(["ab  ", "    "])
    assert core.frame_text(fr) == "ab\n"


# ------------------------------------------------------------- write_json ---
def test_write_json_with_flipped(tmp_path):
    fr = make_frame(["ab", "cd"])
    out = tmp_path / "walk.frames.json"
    core.write_json([fr], "walk", str(out), flipped_too=True)
    data = json.loads(out.read_text())
    assert set(data) == {"walk", "walk_flipped"}
    assert data["walk_flipped"][0]["chars"] == ["ba", "dc"]


# -------------------------------------------------------------------- cli ---
def test_cli_version(capsys):
    from jammsprite import __version__
    from jammsprite.cli import main
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_cli_rejects_bad_tint(tmp_path, capsys):
    from jammsprite.cli import main
    img = tmp_path / "x.png"
    from PIL import Image
    Image.new("RGB", (10, 10)).save(img)
    with pytest.raises(SystemExit) as exc:
        main([str(img), "--tint", "not-a-color"])
    assert exc.value.code != 0


# --------------------------------------------------------------- charsets ---
def test_parse_charset_named_preset():
    assert core.parse_charset("blocks") == core.CHARSETS["blocks"]
    assert core.parse_charset("BLOCKS") == core.CHARSETS["blocks"]  # case-insensitive


def test_parse_charset_literal_passthrough():
    # a string that isn't a preset name is used verbatim as the ramp
    assert core.parse_charset(" .oO@") == " .oO@"


@pytest.mark.parametrize("bad", ["", " ", "x"])
def test_parse_charset_rejects_too_short(bad):
    with pytest.raises(ValueError):
        core.parse_charset(bad)


def test_every_preset_is_usable():
    # each preset must have a reserved slot + at least one real glyph
    for name, ramp in core.CHARSETS.items():
        assert len(ramp) >= 2, name
        assert ramp[0] == " ", f"{name} should start with the reserved empty slot"


# ----------------------------------------------------------- invert_ramp ---
def test_invert_ramp_reverses_body_keeps_slot0():
    assert core.invert_ramp(" .:-=") == " =-:."   # slot 0 stays, rest flips


def test_invert_ramp_twice_is_identity():
    for ramp in (core.CHARSETS["classic"], " .oO@", " ░▒▓█"):
        assert core.invert_ramp(core.invert_ramp(ramp)) == ramp


def test_invert_ramp_short_is_noop():
    assert core.invert_ramp("x") == "x"


# ---------------------------------------------------------------- gamma ----
def test_config_gamma_default_is_one():
    assert core.Config().gamma == 1.0


def test_config_accepts_gamma():
    assert core.Config(gamma=2.2).gamma == 2.2


# ------------------------------------------------------- cli new flags -----
def test_cli_list_charsets_exits_clean(capsys):
    from jammsprite.cli import main
    with pytest.raises(SystemExit) as exc:
        main(["--list-charsets"])          # no input arg required
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "blocks" in out and "classic" in out


def test_cli_charset_and_ramp_and_gamma_reach_config(monkeypatch):
    # verify the CLI plumbs --charset / --invert / --gamma into the Config
    import jammsprite.cli as cli
    seen = {}

    def fake_frames_from_still(path, breathe):
        from PIL import Image
        return [Image.new("RGB", (8, 8))]

    def fake_frame_to_ascii(im, cfg):
        seen["ramp"] = cfg.ramp
        seen["gamma"] = cfg.gamma
        return None  # -> "no subject found", exits before writing outputs

    monkeypatch.setattr(cli.core, "frames_from_still", fake_frames_from_still)
    monkeypatch.setattr(cli.core, "frame_to_ascii", fake_frame_to_ascii)
    with pytest.raises(SystemExit):
        cli.main(["pic.png", "--charset", "blocks", "--invert", "--gamma", "1.8"])
    assert seen["ramp"] == core.invert_ramp(core.CHARSETS["blocks"])
    assert seen["gamma"] == 1.8

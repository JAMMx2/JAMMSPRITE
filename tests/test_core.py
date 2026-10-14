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


# ========================================================== v1.3.0 ==========
# ------------------------------------------------------- quantize_levels ---
def test_quantize_none_matches_plain_rounding():
    t = np.array([[0.0, 0.24, 0.26, 0.5, 1.0]], dtype=np.float32)
    m = np.ones_like(t, dtype=bool)
    out = core.quantize_levels(t, m, levels=5, dither="none")   # span 4
    assert out.tolist() == [[0, 1, 1, 2, 4]]


def test_quantize_masks_out_cells():
    t = np.ones((2, 2), dtype=np.float32)
    m = np.array([[True, False], [False, True]])
    out = core.quantize_levels(t, m, 3, "none")
    assert out.tolist() == [[2, 0], [0, 2]]


@pytest.mark.parametrize("mode", ["ordered", "floyd"])
def test_quantize_dither_preserves_mean_brightness(mode):
    # a flat mid-gray field must dither to roughly the same average level
    t = np.full((16, 16), 0.37, dtype=np.float32)
    m = np.ones_like(t, dtype=bool)
    out = core.quantize_levels(t, m, levels=3, dither=mode)      # span 2 -> want mean ~0.74
    assert out.min() >= 0 and out.max() <= 2
    assert abs(out.mean() - 0.74) < 0.08
    assert len(np.unique(out)) > 1                                # it actually dithered


def test_quantize_ordered_is_deterministic():
    t = np.random.default_rng(1).random((8, 8)).astype(np.float32)
    m = np.ones_like(t, dtype=bool)
    a = core.quantize_levels(t, m, 6, "ordered")
    b = core.quantize_levels(t, m, 6, "ordered")
    assert (a == b).all()


def test_quantize_rejects_unknown_mode():
    with pytest.raises(ValueError):
        core.quantize_levels(np.zeros((1, 1)), np.ones((1, 1), bool), 3, "random")


def test_config_dither_default_none():
    assert core.Config().dither == "none"
    assert "floyd" in core.DITHERS and "ordered" in core.DITHERS


# --------------------------------------------------------- place_on_grid ---
def test_place_on_grid_solid_fill_and_despeckle():
    cfg = core.Config(cols=12, rows=8, ramp=" .oO@")
    L = np.array([[0, 100, 255], [0, 100, 255]], dtype=np.uint8)
    M = np.full((2, 3), 255, dtype=np.uint8)
    C = np.zeros((2, 3, 3), dtype=np.uint8)
    C[..., 0] = 200
    fr = core.place_on_grid(L, M, C, ox=4, oy=3, cfg=cfg)
    assert fr["chars"][3][4:7] == ".o@"       # darkest filled glyph is slot 1, never blank
    assert fr["chars"][4][4:7] == ".o@"
    assert fr["colors"][3][4] == "200,0,0"
    assert fr["chars"][0] == " " * 12


def test_place_on_grid_clips_to_grid():
    cfg = core.Config(cols=4, rows=4, ramp=" .@")
    L = np.full((3, 3), 255, dtype=np.uint8)
    M = np.full((3, 3), 255, dtype=np.uint8)
    C = np.zeros((3, 3, 3), dtype=np.uint8)
    fr = core.place_on_grid(L, M, C, ox=2, oy=-1, cfg=cfg)        # hangs off top + right
    assert len(fr["chars"]) == 4 and all(len(r) == 4 for r in fr["chars"])
    assert fr["chars"][0] == "  @@" and fr["chars"][2] == "    "


# --------------------------------------------------------- dedupe_frames ---
def test_dedupe_drops_consecutive_and_loop_seam():
    a, b, c = make_frame(["a "]), make_frame(["b "]), make_frame(["a "])
    out = core.dedupe_frames([a, a, b, b, b, c])
    assert [f["chars"] for f in out] == [["a "], ["b "]]       # trailing 'a' == first -> dropped


def test_dedupe_keeps_single_and_empty():
    assert core.dedupe_frames([]) == []
    a = make_frame(["x"])
    assert core.dedupe_frames([a, a, a]) == [a]


# ----------------------------------------------------------- trim_frames ---
def test_trim_frames_uses_shared_bbox():
    f1 = make_frame(["      ", "  ab  ", "      ", "      "])
    f2 = make_frame(["      ", "      ", "    cd", "      "])
    out = core.trim_frames([f1, f2])
    assert out[0]["chars"] == ["ab  ", "    "]
    assert out[1]["chars"] == ["    ", "  cd"]
    assert out[0]["colors"][0] == ["1,2,3", "1,2,3", None, None]
    assert f1["chars"][1] == "  ab  "                             # input untouched


def test_trim_frames_pad_is_clamped():
    f = make_frame(["   ", " x ", "   "])
    out = core.trim_frames([f], pad=5)
    assert out[0]["chars"] == ["   ", " x ", "   "]


def test_trim_frames_all_blank_is_noop():
    f = make_frame(["  ", "  "])
    assert core.trim_frames([f])[0]["chars"] == ["  ", "  "]


# ------------------------------------------------------------ frame_ansi ---
def test_frame_ansi_truecolor_runs_and_reset():
    fr = {"chars": ["ab c  "], "colors": [["1,2,3", "1,2,3", None, "9,8,7", None, None]]}
    s = core.frame_ansi(fr, color=True)
    assert s == "\x1b[38;2;1;2;3mab\x1b[0m \x1b[38;2;9;8;7mc\x1b[0m"


def test_frame_ansi_tint_and_plain():
    fr = make_frame(["xy"])
    assert core.frame_ansi(fr, color=False, fg=(255, 0, 0)) == "\x1b[38;2;255;0;0mxy\x1b[0m"
    assert core.frame_ansi(fr, color=False, fg=None) == "xy"


def test_write_ansi_separates_frames_with_formfeed(tmp_path):
    p = tmp_path / "a.ans"
    core.write_ansi([make_frame(["a"]), make_frame(["b"])], str(p), color=False, fg=None)
    assert p.read_text(encoding="utf-8") == "a\n\fb\n"


# ------------------------------------------------------------- html / svg --
def test_render_svg_is_valid_xml_and_animated():
    import xml.etree.ElementTree as ET
    cfg = core.Config()
    frames = [make_frame(["<a>", " b "]), make_frame([" c ", "d  "])]
    svg = core.render_svg(frames, cfg, color=True, duration=100)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg")
    assert "&lt;a&gt;" in svg                                     # escaped glyphs
    assert svg.count("<animate") == 2 and 'dur="200ms"' in svg
    assert 'fill="#010203"' in svg                                # per-cell colour kept


def test_render_svg_single_frame_has_no_animation():
    svg = core.render_svg([make_frame(["x"])], core.Config(), color=False)
    assert "<animate" not in svg and 'fill="#7cffb2"' in svg


def test_render_html_embeds_frames_and_escapes():
    cfg = core.Config(fg=(1, 2, 3), bg=(0, 0, 0))
    html = core.render_html([make_frame(["</script>"]), make_frame(["ok"])], "walk", cfg, color=False, duration=90)
    assert "<!doctype html>" in html and "setInterval(show,90)" in html
    assert "</script>\"" not in html.split("var frames=")[1].split(";")[0]   # payload can't close the tag
    assert "&lt;/script&gt;" in html and "color:#010203" in html


def test_render_html_color_mode_uses_spans():
    html = core.render_html([make_frame(["ab"])], "x", core.Config(), color=True)
    assert "color:#010203" in html and "<span" in html.replace("<\\/", "</")


# ----------------------------------------------------------- read_json -----
def test_read_json_picks_first_or_named(tmp_path):
    p = tmp_path / "w.frames.json"
    core.write_json([make_frame(["ab"])], "walk", str(p), flipped_too=True)
    name, frames = core.read_json(str(p))
    assert name == "walk" and frames[0]["chars"] == ["ab"]
    name2, frames2 = core.read_json(str(p), "walk_flipped")
    assert name2 == "walk_flipped" and frames2[0]["chars"] == ["ba"]
    with pytest.raises(ValueError):
        core.read_json(str(p), "nope")


def test_read_json_rejects_junk(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text('{"x": [1, 2]}')
    with pytest.raises(ValueError):
        core.read_json(str(p))


# ------------------------------------------------------------ even_picks ---
def test_even_picks():
    assert core.even_picks(3, 0) == [0, 1, 2]
    assert core.even_picks(3, 10) == [0, 1, 2]
    assert core.even_picks(10, 4) == [0, 2, 5, 7]


def test_frames_from_folder_honours_limit(tmp_path):
    from PIL import Image
    for i in range(6):
        Image.new("RGB", (4, 4)).save(tmp_path / f"f{i}.png")
    assert len(core.frames_from_folder(str(tmp_path), 3)) == 3
    assert len(core.frames_from_folder(str(tmp_path))) == 6


# ------------------------------------------------------------- cli 1.3 ----
def test_cli_dither_reaches_config(monkeypatch):
    import jammsprite.cli as cli
    seen = {}

    def fake_still(path, breathe):
        from PIL import Image
        return [Image.new("RGB", (8, 8))]

    def fake_to_ascii(im, cfg):
        seen["dither"] = cfg.dither
        return None

    monkeypatch.setattr(cli.core, "frames_from_still", fake_still)
    monkeypatch.setattr(cli.core, "frame_to_ascii", fake_to_ascii)
    with pytest.raises(SystemExit):
        cli.main(["pic.png", "--dither", "floyd"])
    assert seen["dither"] == "floyd"


def test_cli_rejects_bad_dither():
    from jammsprite.cli import main
    with pytest.raises(SystemExit) as exc:
        main(["pic.png", "--dither", "random"])
    assert exc.value.code != 0


def test_cli_writes_new_formats_and_dedupes(tmp_path, monkeypatch):
    import jammsprite.cli as cli
    fr = make_frame(["    ", " ab ", "    "])

    def fake_still(path, breathe):
        from PIL import Image
        return [Image.new("RGB", (8, 8))] * 3

    monkeypatch.setattr(cli.core, "frames_from_still", fake_still)
    monkeypatch.setattr(cli.core, "frame_to_ascii", lambda im, cfg: dict(chars=list(fr["chars"]),
                                                                           colors=[list(r) for r in fr["colors"]]))
    cli.main(["pic.png", "--name", "t", "--out", str(tmp_path), "--no-gif", "--no-sheet",
              "--html", "--svg", "--ansi", "--dedupe", "--trim"])
    data = json.loads((tmp_path / "t.frames.json").read_text())
    assert len(data["t"]) == 1                                    # 3 identical frames -> 1
    assert data["t"][0]["chars"] == ["ab"]                        # trimmed
    for ext in (".html", ".svg", ".ans"):
        assert (tmp_path / ("t" + ext)).exists()


def test_cli_preview_plays_json(tmp_path, monkeypatch):
    import jammsprite.cli as cli
    p = tmp_path / "w.frames.json"
    core.write_json([make_frame(["ab"]), make_frame(["cd"])], "walk", str(p))
    seen = {}

    def fake_play(frames, duration_ms, loops, color, fg):
        seen.update(n=len(frames), d=duration_ms, loops=loops, color=color, fg=fg, first=frames[0]["chars"])

    monkeypatch.setattr(cli.core, "play_in_terminal", fake_play)
    cli.main(["preview", str(p), "--loops", "2", "--duration-ms", "50", "--tint", "amber", "--flip"])
    assert seen == dict(n=2, d=50, loops=2, color=False, fg=core.TINTS["amber"], first=["ba"])


def test_cli_preview_missing_file_exits_nonzero():
    from jammsprite.cli import main
    with pytest.raises(SystemExit) as exc:
        main(["preview", "/nonexistent/x.frames.json"])
    assert exc.value.code != 0

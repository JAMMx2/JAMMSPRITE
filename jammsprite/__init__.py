"""JAMMSPRITE — turn real video or photos into clean, animated ASCII sprites."""

__version__ = "1.3.0"

__all__ = [
    "__version__",
    "Config",
    "TINTS",
    "CHARSETS",
    "DITHERS",
    "clean_mask",
    "dedupe_frames",
    "despeckle",
    "flip_frame",
    "frame_ansi",
    "frame_to_ascii",
    "frame_text",
    "invert_ramp",
    "parse_charset",
    "parse_tint",
    "play_in_terminal",
    "quantize_levels",
    "read_json",
    "render_gif",
    "render_html",
    "render_sheet",
    "render_svg",
    "trim_frames",
    "write_ansi",
    "write_json",
]


def __getattr__(name):
    # Lazy re-export so `import jammsprite` stays instant and dependency-free
    # (numpy/PIL/scipy load only when the pipeline is actually used).
    if name in __all__ and name != "__version__":
        from . import core
        return getattr(core, name)
    raise AttributeError(f"module 'jammsprite' has no attribute {name!r}")

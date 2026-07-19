"""JAMMSPRITE — turn real video or photos into clean, animated ASCII sprites."""

__version__ = "1.1.1"

__all__ = [
    "__version__",
    "Config",
    "TINTS",
    "clean_mask",
    "despeckle",
    "flip_frame",
    "frame_to_ascii",
    "frame_text",
    "parse_tint",
    "render_gif",
    "render_sheet",
    "write_json",
]


def __getattr__(name):
    # Lazy re-export so `import jammsprite` stays instant and dependency-free
    # (numpy/PIL/scipy load only when the pipeline is actually used).
    if name in __all__ and name != "__version__":
        from . import core
        return getattr(core, name)
    raise AttributeError(f"module 'jammsprite' has no attribute {name!r}")

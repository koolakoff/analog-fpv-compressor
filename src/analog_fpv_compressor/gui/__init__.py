"""Optional desktop entry point; CLI and core do not import Qt."""


def main():
    """Start the desktop application after checking its optional dependency."""
    try:
        from .window import launch
    except ModuleNotFoundError as error:
        if error.name and error.name.startswith("PySide6"):
            raise SystemExit('GUI requires PySide6. Install with: python -m pip install ".[gui]"') from error
        raise
    return launch()

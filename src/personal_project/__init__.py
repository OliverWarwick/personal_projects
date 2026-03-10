"""Personal project.

On import this module will attempt to read a repository-level file named
`copilot-instructions.md` (if present) and expose its contents as the
module-level variable ``__copilot_instructions__``. This read is optional
and failures are silently ignored so import-time behaviour remains stable
in CI and test environments.
"""

try:
    # Prefer installed package metadata
    from importlib.metadata import version, PackageNotFoundError

    try:
        __version__ = version("personal-project")
    except PackageNotFoundError:  # package not installed, fall back to local _version
        from ._version import __version__
except Exception:
    # Last-resort fallback if importlib.metadata is unavailable
    try:
        from ._version import __version__
    except Exception:
        __version__ = "0.0.0"

# Attempt to read optional copilot instructions from the repository root.
# This is non-critical and will not raise if the file is missing or unreadable.
try:
    from pathlib import Path
    _repo_root = Path(__file__).resolve().parents[2]
    _copilot_path = _repo_root / "copilot-instructions.md"
    if _copilot_path.exists():
        try:
            __copilot_instructions__ = _copilot_path.read_text(encoding="utf-8")
        except Exception:
            __copilot_instructions__ = None
    else:
        __copilot_instructions__ = None
except Exception:
    __copilot_instructions__ = None

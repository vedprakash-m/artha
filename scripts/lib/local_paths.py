"""
scripts/lib/local_paths.py — machine-local locations that must never sync (WP-03)
================================================================================
Plaintext working data (fetched email bodies, derived JSONL) belongs outside the
cloud-synced workspace: ``~/.artha-local`` (or ``$ARTHA_LOCAL_DIR``).  This module
is the one place that decides where, so the pipeline's ``--output`` validator and
every consumer change together (re-artha §12.2 item 6, P1).
"""
from __future__ import annotations

import os
from pathlib import Path

PIPELINE_OUTPUT_NAME = "pipeline_output.jsonl"


def local_dir() -> Path:
    """The machine-local directory, created ``0700`` on first use."""
    root = Path(os.environ.get("ARTHA_LOCAL_DIR") or Path.home() / ".artha-local").expanduser()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def pipeline_output_path() -> Path:
    return local_dir() / PIPELINE_OUTPUT_NAME


def resolve_local_output(raw: str | os.PathLike[str]) -> Path:
    """Resolve a requested output path and require it to be inside :func:`local_dir`.

    Relative paths are taken relative to the local directory.  Raises ``ValueError``
    for anything that resolves elsewhere (traversal, workspace, sync folders).
    """
    base = local_dir().resolve()
    candidate = Path(raw).expanduser()
    target = (candidate if candidate.is_absolute() else base / candidate).resolve()
    if not target.is_relative_to(base):
        raise ValueError(
            f"--output must be inside the machine-local directory {base} (got {str(raw)!r}); "
            "plaintext pipeline output must not live in the synced workspace."
        )
    return target

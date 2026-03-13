"""Configuration helpers — pattern loading and safety checks."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

from ._utils import log


@dataclass
class Pattern:
    """A search pattern with case-sensitivity metadata."""

    text: str
    case_sensitive: bool = False

    def __str__(self) -> str:
        return self.text


def load_patterns(path: str, *, case_sensitive: bool = False) -> List[Pattern]:
    """Read *path*, strip comments (``#``) and blank lines, return a list of
    :class:`Pattern` objects.

    Supports per-pattern case-sensitivity flags via prefix syntax:

    - ``cs:GoTo``  — force case-sensitive for this pattern
    - ``ci:goto``  — force case-insensitive for this pattern
    - ``GoTo``     — uses the *case_sensitive* default

    The *case_sensitive* parameter controls the default for patterns without
    a prefix.
    """
    patterns: List[Pattern] = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            # Per-pattern overrides
            if line.startswith("cs:"):
                patterns.append(Pattern(text=line[3:], case_sensitive=True))
            elif line.startswith("ci:"):
                patterns.append(Pattern(text=line[3:], case_sensitive=False))
            else:
                patterns.append(Pattern(text=line, case_sensitive=case_sensitive))
    return patterns


def ensure_patterns_file_gitignored(patterns_path: str) -> None:
    """Hard-exit if the patterns file is NOT listed in ``.gitignore``.

    This is a safety check — the patterns file typically contains sensitive
    strings that must never be committed.
    """
    patterns_path_obj = Path(patterns_path)

    # Walk up to find the repo root (directory containing .git/)
    search = patterns_path_obj.resolve().parent
    repo_root = None
    while True:
        if (search / ".git").exists():
            repo_root = search
            break
        parent = search.parent
        if parent == search:
            break
        search = parent

    if repo_root is None:
        log("Cannot locate repo root — skipping .gitignore safety check", level="WARN")
        return

    gitignore_path = repo_root / ".gitignore"
    if not gitignore_path.exists():
        log(
            f"No .gitignore found at {gitignore_path}.  "
            f"Add '{patterns_path}' to .gitignore before continuing.",
            level="ERROR",
        )
        sys.exit(1)

    # Compute the relative path of the patterns file from the repo root
    try:
        rel = patterns_path_obj.resolve().relative_to(repo_root)
    except ValueError:
        rel = patterns_path_obj

    rel_str = str(rel)
    # Also check with leading slash and without
    variants = {rel_str, f"/{rel_str}", rel_str.lstrip("/")}

    with open(gitignore_path, "r", encoding="utf-8") as fh:
        for raw_line in fh:
            entry = raw_line.strip()
            if entry in variants:
                return  # Good — it is gitignored

    log(
        f"SAFETY: '{rel_str}' is NOT in {gitignore_path}.\n"
        f"    Add it to .gitignore to prevent accidental commit of sensitive patterns.\n"
        f"    Refusing to continue.",
        level="ERROR",
    )
    sys.exit(1)

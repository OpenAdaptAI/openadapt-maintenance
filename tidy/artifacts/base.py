"""Base classes for artifact scanning and cleaning.

All artifact scanners follow the same pattern:
1. ``scan()`` — find pattern matches in the artifact source, return matches
2. ``clean()`` — remove or redact artifacts containing matches (requires --confirm)
3. ``report()`` — format scan results for display

Every scanner supports dry-run mode by default: ``clean()`` only acts when
``confirm=True`` is passed.  Without it, it reports what *would* be deleted.
"""

from __future__ import annotations

import enum
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .._config import Pattern
from .._utils import log


class ArtifactType(enum.Enum):
    """Supported artifact types."""

    GITHUB_RELEASES = "github-releases"
    GITHUB_ACTIONS = "github-actions"
    PYPI = "pypi"
    DOCKER_GHCR = "docker-ghcr"


@dataclass
class ArtifactMatch:
    """One pattern hit inside a build artifact.

    Attributes:
        artifact_type: The type of artifact (release, action, pypi, docker).
        source: Human-readable identifier for the artifact (e.g., release tag,
            workflow run ID, PyPI version, image tag).
        location: Where inside the artifact the match was found (e.g.,
            asset filename, log step name, file path within wheel).
        line: The actual text containing the match.
        pattern: The pattern that matched.
        artifact_id: API identifier needed for deletion (e.g., asset ID,
            artifact ID, package version ID).
        metadata: Extra data needed for cleaning (e.g., release ID, run ID).
    """

    artifact_type: ArtifactType
    source: str
    location: str
    line: str
    pattern: str
    artifact_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ArtifactScanner(ABC):
    """Abstract base for artifact scanners.

    Subclasses implement ``scan()`` and ``clean()`` for a specific artifact
    type (GitHub Releases, GitHub Actions, PyPI, Docker/GHCR).
    """

    artifact_type: ArtifactType

    def __init__(self, patterns: List[Pattern], repo: str) -> None:
        """Initialize the scanner.

        Args:
            patterns: List of Pattern objects (from _config.load_patterns).
            repo: Repository identifier.  Format depends on subclass:
                - GitHub: ``owner/repo``
                - PyPI: package name
                - Docker: ``owner/image``
        """
        self.patterns = patterns
        self.repo = repo

    def _match_text(self, text: str, pattern: Pattern) -> bool:
        """Check if *text* contains *pattern*, respecting case sensitivity."""
        if pattern.case_sensitive:
            return pattern.text in text
        return pattern.text.lower() in text.lower()

    def _scan_text(
        self,
        text: str,
        *,
        source: str,
        location: str,
        artifact_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[ArtifactMatch]:
        """Scan *text* for all patterns, returning matches."""
        matches: List[ArtifactMatch] = []
        for line in text.splitlines():
            for pattern in self.patterns:
                if self._match_text(line, pattern):
                    matches.append(
                        ArtifactMatch(
                            artifact_type=self.artifact_type,
                            source=source,
                            location=location,
                            line=line.strip(),
                            pattern=pattern.text,
                            artifact_id=artifact_id,
                            metadata=metadata or {},
                        )
                    )
        return matches

    @abstractmethod
    def scan(self) -> List[ArtifactMatch]:
        """Scan all artifacts for pattern matches.

        Returns a list of ArtifactMatch objects.  This is always safe to run
        (read-only operation).
        """
        ...

    @abstractmethod
    def clean(
        self,
        matches: List[ArtifactMatch],
        *,
        confirm: bool = False,
        replacement: str = "[REDACTED]",
    ) -> List[str]:
        """Clean (delete/redact) artifacts that contain matches.

        Args:
            matches: The matches to clean (as returned by ``scan()``).
            confirm: If False (default), only report what would be done
                (dry-run).  If True, actually perform the deletions.
            replacement: Replacement text for redaction (where applicable).

        Returns:
            A list of human-readable action descriptions (what was done or
            what would be done).
        """
        ...

    def report(self, matches: List[ArtifactMatch]) -> str:
        """Format scan results as a human-readable report."""
        if not matches:
            return f"  No matches found in {self.artifact_type.value}."

        lines: List[str] = []
        lines.append(f"  {len(matches)} match(es) in {self.artifact_type.value}:")
        lines.append("")

        # Group by source
        by_source: Dict[str, List[ArtifactMatch]] = {}
        for m in matches:
            by_source.setdefault(m.source, []).append(m)

        for source, source_matches in by_source.items():
            lines.append(f"    {source}:")
            for m in source_matches:
                loc = f" [{m.location}]" if m.location else ""
                line_preview = m.line[:80] + ("..." if len(m.line) > 80 else "")
                lines.append(
                    f"      pattern='{m.pattern}'{loc}: {line_preview}"
                )
            lines.append("")

        return "\n".join(lines)

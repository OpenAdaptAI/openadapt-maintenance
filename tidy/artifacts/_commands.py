"""CLI command handlers for artifact scanning and cleaning.

These functions are called by the CLI dispatcher in ``tidy.py`` and follow
the same signature pattern as the existing ``cmd_scan``, ``cmd_clean``, etc.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Set

from .base import ArtifactMatch, ArtifactScanner, ArtifactType
from .github_releases import GitHubReleasesScanner
from .github_actions import GitHubActionsScanner
from .pypi import PyPIScanner
from .docker_ghcr import DockerGHCRScanner
from .._config import Pattern, ensure_patterns_file_gitignored, load_patterns
from .._utils import confirm, log


# ─── Scanner registry ──────────────────────────────────────────────────

_SCANNER_NAMES = {
    "releases": ArtifactType.GITHUB_RELEASES,
    "actions": ArtifactType.GITHUB_ACTIONS,
    "pypi": ArtifactType.PYPI,
    "docker": ArtifactType.DOCKER_GHCR,
}


def _create_scanners(
    artifact_types: List[str],
    patterns: List[Pattern],
    repo: str,
    *,
    package: Optional[str] = None,
    affected_shas: Optional[Set[str]] = None,
    max_runs: int = 100,
    max_artifacts: int = 200,
    is_org: bool = True,
) -> List[ArtifactScanner]:
    """Create scanner instances for the requested artifact types.

    Args:
        artifact_types: List of type names ("releases", "actions", "pypi",
            "docker") or ["all"] for everything.
        patterns: Loaded patterns.
        repo: Repository in ``owner/repo`` format.
        package: PyPI package name (for pypi scanner).
        affected_shas: Commit SHAs known to contain sensitive data.
        max_runs: Max workflow runs to scan (actions scanner).
        max_artifacts: Max artifacts to scan (actions scanner).
        is_org: Whether the repo owner is an org or user.

    Returns:
        List of scanner instances.
    """
    if "all" in artifact_types:
        types_to_create = list(_SCANNER_NAMES.values())
    else:
        types_to_create = []
        for name in artifact_types:
            if name not in _SCANNER_NAMES:
                log(f"Unknown artifact type: {name!r}", level="ERROR")
                log(f"Valid types: {', '.join(_SCANNER_NAMES.keys())}, all", level="INFO")
                sys.exit(1)
            types_to_create.append(_SCANNER_NAMES[name])

    scanners: List[ArtifactScanner] = []
    for atype in types_to_create:
        if atype == ArtifactType.GITHUB_RELEASES:
            scanners.append(GitHubReleasesScanner(patterns, repo))
        elif atype == ArtifactType.GITHUB_ACTIONS:
            scanners.append(
                GitHubActionsScanner(
                    patterns,
                    repo,
                    max_runs=max_runs,
                    max_artifacts=max_artifacts,
                )
            )
        elif atype == ArtifactType.PYPI:
            scanners.append(PyPIScanner(patterns, repo, package=package))
        elif atype == ArtifactType.DOCKER_GHCR:
            scanners.append(
                DockerGHCRScanner(
                    patterns,
                    repo,
                    affected_shas=affected_shas,
                    is_org=is_org,
                )
            )
    return scanners


# ─── Command handlers ──────────────────────────────────────────────────


def cmd_scan_artifacts(args: Any) -> None:
    """Scan build artifacts for sensitive patterns."""
    ensure_patterns_file_gitignored(args.patterns)
    patterns = load_patterns(
        args.patterns, case_sensitive=getattr(args, "case_sensitive", False)
    )
    if not patterns:
        log("No patterns loaded — nothing to scan.", level="WARN")
        return

    repo = args.repo
    artifact_types = args.types

    log(f"Scanning {', '.join(artifact_types)} artifacts for {repo}...")
    log(f"Patterns: {len(patterns)}")
    print(file=sys.stderr)

    scanners = _create_scanners(
        artifact_types,
        patterns,
        repo,
        package=getattr(args, "package", None),
        max_runs=getattr(args, "max_runs", 100),
        max_artifacts=getattr(args, "max_artifacts", 200),
    )

    all_matches: List[ArtifactMatch] = []
    for scanner in scanners:
        matches = scanner.scan()
        all_matches.extend(matches)
        print(file=sys.stderr)

    # Report
    print(file=sys.stderr)
    log("=" * 60)
    log("ARTIFACT SCAN REPORT")
    log("=" * 60)
    print(file=sys.stderr)

    if not all_matches:
        log("No matches found in any artifacts.", level="OK")
    else:
        log(f"TOTAL: {len(all_matches)} match(es) found across artifacts", level="WARN")
        print(file=sys.stderr)

        # Group by type and print reports
        by_type: Dict[ArtifactType, List[ArtifactMatch]] = {}
        for m in all_matches:
            by_type.setdefault(m.artifact_type, []).append(m)

        for atype, matches in by_type.items():
            # Find the scanner for this type
            for scanner in scanners:
                if scanner.artifact_type == atype:
                    print(scanner.report(matches), file=sys.stderr)
                    break

    # JSON output
    if getattr(args, "json", False):
        output = {
            "repo": repo,
            "total_matches": len(all_matches),
            "matches": [
                {
                    "artifact_type": m.artifact_type.value,
                    "source": m.source,
                    "location": m.location,
                    "pattern": m.pattern,
                    "line": m.line[:200],
                    "artifact_id": m.artifact_id,
                }
                for m in all_matches
            ],
        }
        print(json.dumps(output, indent=2))


def cmd_clean_artifacts(args: Any) -> None:
    """Scan and clean build artifacts containing sensitive patterns."""
    ensure_patterns_file_gitignored(args.patterns)
    patterns = load_patterns(
        args.patterns, case_sensitive=getattr(args, "case_sensitive", False)
    )
    if not patterns:
        log("No patterns loaded — nothing to clean.", level="WARN")
        return

    repo = args.repo
    artifact_types = args.types
    do_confirm = getattr(args, "confirm", False)
    replacement = getattr(args, "replacement", "[REDACTED]")

    log(f"Scanning {', '.join(artifact_types)} artifacts for {repo}...")
    log(f"Patterns: {len(patterns)}")
    if not do_confirm:
        log("DRY-RUN MODE: pass --confirm to actually delete/redact", level="WARN")
    print(file=sys.stderr)

    scanners = _create_scanners(
        artifact_types,
        patterns,
        repo,
        package=getattr(args, "package", None),
        max_runs=getattr(args, "max_runs", 100),
        max_artifacts=getattr(args, "max_artifacts", 200),
    )

    all_matches: List[ArtifactMatch] = []
    for scanner in scanners:
        matches = scanner.scan()
        all_matches.extend(matches)
        print(file=sys.stderr)

    if not all_matches:
        log("No matches found — nothing to clean.", level="OK")
        return

    log(f"Found {len(all_matches)} match(es) across artifacts", level="WARN")
    print(file=sys.stderr)

    # Show what will be done
    by_type: Dict[ArtifactType, List[ArtifactMatch]] = {}
    for m in all_matches:
        by_type.setdefault(m.artifact_type, []).append(m)

    for atype, matches in by_type.items():
        for scanner in scanners:
            if scanner.artifact_type == atype:
                print(scanner.report(matches), file=sys.stderr)
                break

    # Confirm before cleaning
    if do_confirm:
        if not getattr(args, "yes", False):
            print(file=sys.stderr)
            if not confirm(
                f"About to clean {len(all_matches)} match(es) across "
                f"{len(by_type)} artifact type(s). Continue?"
            ):
                log("Aborted by user.", level="INFO")
                return

    # Clean
    print(file=sys.stderr)
    log("=" * 60)
    log("CLEANING ARTIFACTS" if do_confirm else "DRY-RUN CLEANING REPORT")
    log("=" * 60)
    print(file=sys.stderr)

    all_actions: List[str] = []
    for scanner in scanners:
        type_matches = by_type.get(scanner.artifact_type, [])
        if type_matches:
            actions = scanner.clean(
                type_matches,
                confirm=do_confirm,
                replacement=replacement,
            )
            all_actions.extend(actions)
            print(file=sys.stderr)

    print(file=sys.stderr)
    log(f"{'Completed' if do_confirm else 'Would perform'} {len(all_actions)} action(s)")

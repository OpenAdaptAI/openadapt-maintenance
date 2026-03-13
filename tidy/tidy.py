"""CLI entry point for the *tidy* git-history scrubbing tool.

Usage::

    python -m tidy scan      --patterns tidy/patterns
    python -m tidy plan      --patterns tidy/patterns --replacement "Enterprise"
    python -m tidy clean     --patterns tidy/patterns --replacement "Enterprise"
    python -m tidy verify    --patterns tidy/patterns
    python -m tidy ticket    --commit-map /path/to/commit-map
    python -m tidy scan-org  --org OpenAdaptAI --patterns tidy/patterns
    python -m tidy clean-org --org OpenAdaptAI --patterns tidy/patterns --replacement "Enterprise"

Or via the ``tidy`` console_script (if installed)::

    tidy scan --patterns tidy/patterns
"""

from __future__ import annotations

import argparse
import sys

from ._core import (
    cmd_clean,
    cmd_clean_org,
    cmd_plan,
    cmd_scan,
    cmd_scan_org,
    cmd_ticket,
    cmd_verify,
)


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add arguments common to scan/plan/clean/verify."""
    parser.add_argument(
        "--patterns",
        required=True,
        help="Path to the patterns file (one pattern per line).",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        default=False,
        help=(
            "Default to case-sensitive matching for patterns without a "
            "cs:/ci: prefix. Without this flag, matching is case-insensitive "
            "by default (which can cause false positives for CamelCase "
            "patterns matching unrelated lowercase keywords in code)."
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tidy",
        description="Git-history scrubbing tool — scan, plan, clean, verify, ticket, scan-org, clean-org.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── scan ────────────────────────────────────────────────────────
    p_scan = sub.add_parser(
        "scan",
        help="Scan commit messages and file contents for sensitive patterns.",
    )
    _add_common_args(p_scan)
    p_scan.add_argument(
        "--repo",
        default=None,
        help="Path to the git repository (default: current directory).",
    )
    p_scan.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON.",
    )

    # ── plan ────────────────────────────────────────────────────────
    p_plan = sub.add_parser(
        "plan",
        help="Scan + build impact report with before/after preview.",
    )
    _add_common_args(p_plan)
    p_plan.add_argument(
        "--replacement",
        default="[REDACTED]",
        help="Replacement text for scrubbed patterns (default: [REDACTED]).",
    )
    p_plan.add_argument(
        "--repo",
        default=None,
        help="Path to the git repository.",
    )

    # ── clean ───────────────────────────────────────────────────────
    p_clean = sub.add_parser(
        "clean",
        help="Full scrub pipeline: backup, filter-repo, force push.",
    )
    _add_common_args(p_clean)
    p_clean.add_argument(
        "--replacement",
        default="[REDACTED]",
        help="Replacement text for scrubbed patterns (default: [REDACTED]).",
    )
    p_clean.add_argument(
        "--repo",
        default=None,
        help="Path to the git repository.",
    )
    p_clean.add_argument(
        "--yes", "-y",
        action="store_true",
        default=False,
        help="Skip confirmation prompt.",
    )

    # ── verify ──────────────────────────────────────────────────────
    p_verify = sub.add_parser(
        "verify",
        help="Re-scan local repo and optionally verify via GitHub API.",
    )
    _add_common_args(p_verify)
    p_verify.add_argument(
        "--repo",
        default=None,
        help="Path to the git repository.",
    )
    p_verify.add_argument(
        "--remote",
        action="store_true",
        default=False,
        help="Also verify via GitHub API.",
    )
    p_verify.add_argument(
        "--branches",
        nargs="+",
        default=None,
        help="Branches to check remotely (default: all remote branches).",
    )
    p_verify.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max commits to check per branch (default: 100).",
    )

    # ── ticket ──────────────────────────────────────────────────────
    p_ticket = sub.add_parser(
        "ticket",
        help="Generate a GitHub Support ticket for cache purge.",
    )
    p_ticket.add_argument(
        "--repo",
        default=None,
        help="Path to the git repository.",
    )
    p_ticket.add_argument(
        "--shas",
        nargs="+",
        default=None,
        help="Old (dangling) SHA(s) to include in the ticket.",
    )
    p_ticket.add_argument(
        "--commit-map",
        default=None,
        help="Path to git-filter-repo commit-map file.",
    )
    p_ticket.add_argument(
        "--branches",
        nargs="+",
        default=None,
        help="Branch names affected (default: auto-detect).",
    )
    p_ticket.add_argument(
        "--output", "-o",
        default=None,
        help="Write ticket text to file instead of stdout.",
    )

    # ── scan-org ────────────────────────────────────────────────────
    p_scan_org = sub.add_parser(
        "scan-org",
        help="Scan all public repos in a GitHub org for sensitive patterns.",
    )
    _add_common_args(p_scan_org)
    p_scan_org.add_argument(
        "--org",
        required=True,
        help="GitHub organization name.",
    )
    p_scan_org.add_argument(
        "--clone-dir",
        default=None,
        help=(
            "Directory to clone repos into (default: temp dir). "
            "If repos already exist here, they will be used as-is."
        ),
    )
    p_scan_org.add_argument(
        "--include-private",
        action="store_true",
        default=False,
        help="Also scan private repos (default: public only).",
    )
    p_scan_org.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output results as JSON.",
    )

    # ── clean-org ───────────────────────────────────────────────────
    p_clean_org = sub.add_parser(
        "clean-org",
        help="Scan + clean all affected repos in a GitHub org.",
    )
    _add_common_args(p_clean_org)
    p_clean_org.add_argument(
        "--org",
        required=True,
        help="GitHub organization name.",
    )
    p_clean_org.add_argument(
        "--replacement",
        default="[REDACTED]",
        help="Replacement text for scrubbed patterns (default: [REDACTED]).",
    )
    p_clean_org.add_argument(
        "--clone-dir",
        default=None,
        help=(
            "Directory to clone repos into (default: temp dir). "
            "If repos already exist here, they will be used as-is."
        ),
    )
    p_clean_org.add_argument(
        "--include-private",
        action="store_true",
        default=False,
        help="Also clean private repos (default: public only).",
    )
    p_clean_org.add_argument(
        "--yes", "-y",
        action="store_true",
        default=False,
        help="Skip confirmation prompt.",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    """Parse arguments and dispatch to the appropriate subcommand."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "scan": cmd_scan,
        "plan": cmd_plan,
        "clean": cmd_clean,
        "verify": cmd_verify,
        "ticket": cmd_ticket,
        "scan-org": cmd_scan_org,
        "clean-org": cmd_clean_org,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()

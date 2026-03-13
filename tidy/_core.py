"""Core logic for the *tidy* git-history scrubbing tool.

Pipeline: Scan -> Plan -> Clean -> Verify -> Ticket

The module operates on a local git repository and optionally interacts with
GitHub (via ``gh`` CLI) for fork enumeration, branch-protection toggling,
and post-clean verification.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ._config import Pattern, ensure_patterns_file_gitignored, load_patterns
from ._github import (
    count_forks,
    disable_force_push_protection,
    generate_ticket_text,
    list_forks,
    restore_branch_protection,
    verify_all_commits_scrubbed,
)
from ._utils import confirm, log, run_cmd, run_git


# ===================================================================
# Data classes
# ===================================================================


@dataclass
class Match:
    """One pattern hit inside a commit message."""

    sha: str
    subject: str
    line: str
    pattern: str
    line_number: int


@dataclass
class BlobMatch:
    """One pattern hit inside tracked file content."""

    path: str
    line_number: int
    line: str
    pattern: str


@dataclass
class Plan:
    """Full impact report produced by :func:`build_plan`."""

    matches: List[Match]
    affected_commits: int
    downstream_commits: int
    affected_tags: List[str]
    affected_branches: List[str]
    forks: int
    replacement: str
    blob_matches: List[BlobMatch] = field(default_factory=list)


# ===================================================================
# Internal helpers
# ===================================================================


def _extract_repo_from_url(remote_url: str) -> str:
    """Extract ``owner/repo`` from an SSH or HTTPS GitHub URL.

    Handles:
    - ``git@github.com:owner/repo.git``
    - ``https://github.com/owner/repo.git``
    - ``https://github.com/owner/repo``
    """
    # SSH form
    m = re.match(r"git@github\.com:(.+?)(?:\.git)?$", remote_url.strip())
    if m:
        return m.group(1)
    # HTTPS form
    m = re.match(r"https?://github\.com/(.+?)(?:\.git)?$", remote_url.strip())
    if m:
        return m.group(1)
    raise ValueError(f"Cannot parse GitHub repo from remote URL: {remote_url!r}")


def _get_remote_url(cwd: Optional[str] = None) -> str:
    """Return the ``origin`` remote URL."""
    result = run_git("remote", "get-url", "origin", cwd=cwd)
    return result.stdout.strip()


def _short_sha(sha: str) -> str:
    """Return the first 12 characters of *sha*."""
    return sha[:12]


# ===================================================================
# Scanning
# ===================================================================


def scan_commit_messages(
    patterns: List[Pattern],
    refs: str = "--all",
    cwd: Optional[str] = None,
) -> List[Match]:
    """Scan commit messages for *patterns* using ``git log``.

    Returns a list of :class:`Match` objects, one per pattern hit.
    """
    separator = "---END---"
    result = run_git(
        "log",
        refs,
        f"--format=%H%n%s%n%B%n{separator}",
        cwd=cwd,
        check=True,
    )

    matches: List[Match] = []
    raw = result.stdout

    # Split into per-commit blocks
    blocks = raw.split(separator)
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue

        sha = lines[0].strip()
        if not sha or len(sha) < 7:
            continue

        subject = lines[1].strip() if len(lines) > 1 else ""
        # The full message is lines[2:] (the body after subject)
        # But since %B already includes the subject, we scan all lines after sha
        message_lines = lines[1:]

        for line_number, line in enumerate(message_lines, start=1):
            for pattern in patterns:
                if pattern.case_sensitive:
                    found = pattern.text in line
                else:
                    found = pattern.text.lower() in line.lower()
                if found:
                    matches.append(
                        Match(
                            sha=sha,
                            subject=subject,
                            line=line,
                            pattern=pattern.text,
                            line_number=line_number,
                        )
                    )

    return matches


def scan_file_contents(
    patterns: List[Pattern],
    ref: str = "HEAD",
    cwd: Optional[str] = None,
) -> List[BlobMatch]:
    """Scan tracked file contents at *ref* for *patterns* using ``git grep``.

    Respects each pattern's ``case_sensitive`` flag.

    Returns a list of :class:`BlobMatch` objects.
    """
    blob_matches: List[BlobMatch] = []

    for pattern in patterns:
        grep_args = [
            "grep",
            "-n",
            "--fixed-strings",
        ]
        # Only add -i for case-insensitive patterns (bug fix: previously
        # always used -i, causing false positives for CamelCase patterns
        # matching unrelated lowercase keywords in code).
        if not pattern.case_sensitive:
            grep_args.append("-i")
        grep_args.extend([pattern.text, ref])

        result = run_git(
            *grep_args,
            cwd=cwd,
            check=False,  # exit 1 means "no match"
        )
        if result.returncode not in (0, 1):
            log(f"git grep failed for pattern '{pattern.text}': {result.stderr}", level="WARN")
            continue

        for raw_line in result.stdout.splitlines():
            # Format: ref:path:lineno:content
            # or path:lineno:content when ref is empty
            parts = raw_line.split(":", 3)
            if len(parts) < 4:
                continue
            # parts[0] = ref, parts[1] = path, parts[2] = lineno, parts[3] = content
            path = parts[1]
            try:
                lineno = int(parts[2])
            except ValueError:
                continue
            content = parts[3]
            blob_matches.append(
                BlobMatch(
                    path=path,
                    line_number=lineno,
                    line=content,
                    pattern=pattern.text,
                )
            )

    return blob_matches


def scan_file_history(
    patterns: List[Pattern],
    cwd: Optional[str] = None,
) -> List[Match]:
    """Scan file *history* for patterns using ``git log -S``.

    Unlike :func:`scan_file_contents` (which only checks HEAD),
    this finds commits where a pattern was introduced or removed in any
    file across the entire history.  Returns :class:`Match` objects
    (one per pattern/commit pair) so the caller can identify affected repos.

    This is critical for the org-wide scan — a repo may have removed a
    sensitive string from HEAD but still have it in historical blobs.
    ``git filter-repo`` will rewrite those blobs, but the scan must
    detect them first so the repo is flagged for cleaning.
    """
    history_matches: List[Match] = []

    for pattern in patterns:
        git_args = ["log", "--all", "--oneline"]
        if pattern.case_sensitive:
            git_args.append("-S")
            git_args.append(pattern.text)
        else:
            git_args.append("-S")
            git_args.append(pattern.text)
            git_args.append("--regexp-ignore-case")

        # Limit to text/config files to avoid massive binary diffs
        git_args.append("--")
        git_args.extend(["*.md", "*.txt", "*.yml", "*.yaml", "*.json",
                         "*.toml", "*.py", "*.rst", "*.cfg", "*.ini",
                         "*.html"])

        result = run_git(*git_args, cwd=cwd, check=False)
        if result.returncode != 0:
            continue

        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            parts = line.split(None, 1)
            sha = parts[0]
            subject = parts[1] if len(parts) > 1 else ""
            history_matches.append(
                Match(
                    sha=sha,
                    subject=subject,
                    line=f"[file history] {subject}",
                    pattern=pattern.text,
                    line_number=0,
                )
            )

    return history_matches


# ===================================================================
# Planning
# ===================================================================


def build_plan(
    matches: List[Match],
    replacement: str,
    blob_matches: Optional[List[BlobMatch]] = None,
    cwd: Optional[str] = None,
) -> Plan:
    """Compute the full impact report for the matched patterns.

    Counts affected/downstream commits, tags, branches, and forks.
    """
    if blob_matches is None:
        blob_matches = []

    # Unique affected SHAs
    affected_shas = sorted(set(m.sha for m in matches))
    affected_commits = len(affected_shas)

    # Downstream commits — commits that descend from any affected commit
    downstream_shas: set[str] = set()
    for sha in affected_shas:
        result = run_git(
            "rev-list",
            f"{sha}..HEAD",
            cwd=cwd,
            check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                if line.strip():
                    downstream_shas.add(line.strip())
    downstream_commits = len(downstream_shas - set(affected_shas))

    # Affected tags
    affected_tags: List[str] = []
    for sha in affected_shas:
        result = run_git("tag", "--contains", sha, cwd=cwd, check=False)
        if result.returncode == 0:
            for tag in result.stdout.strip().splitlines():
                tag = tag.strip()
                if tag and tag not in affected_tags:
                    affected_tags.append(tag)

    # Affected branches
    affected_branches: List[str] = []
    for sha in affected_shas:
        result = run_git("branch", "--contains", sha, cwd=cwd, check=False)
        if result.returncode == 0:
            for branch_line in result.stdout.strip().splitlines():
                branch = branch_line.strip().lstrip("* ")
                if branch and branch not in affected_branches:
                    affected_branches.append(branch)

    # Forks
    forks = 0
    try:
        remote_url = _get_remote_url(cwd=cwd)
        repo = _extract_repo_from_url(remote_url)
        forks = count_forks(repo)
    except Exception:
        pass  # Not fatal — we just can't count forks

    return Plan(
        matches=matches,
        affected_commits=affected_commits,
        downstream_commits=downstream_commits,
        affected_tags=affected_tags,
        affected_branches=affected_branches,
        forks=forks,
        replacement=replacement,
        blob_matches=blob_matches,
    )


# ===================================================================
# Filter-repo callback generators
# ===================================================================


def _re_flags_for_pattern(pattern: Pattern) -> str:
    """Return the ``re`` flags expression for *pattern*."""
    if pattern.case_sensitive:
        return "0"
    return "re.IGNORECASE"


def _build_message_callback(patterns: List[Pattern], replacement: str) -> str:
    """Generate Python code for ``git filter-repo --message-callback``.

    For multi-word patterns, generates cross-line variants (the pattern
    split at word boundaries with ``\\n``) to handle cases where the pattern
    spans two lines in commit messages.

    Respects each pattern's ``case_sensitive`` flag.
    """
    # Build all search/replace pairs including cross-line variants
    lines: List[str] = []
    lines.append("import re")
    lines.append(f"replacement = {replacement!r}.encode('utf-8')")
    lines.append("msg = message")

    for pattern in patterns:
        flags = _re_flags_for_pattern(pattern)
        # Direct replacement
        lines.append(
            f"msg = re.sub({pattern.text!r}.encode('utf-8'), replacement, msg, flags={flags})"
        )

        # Cross-line variants: for multi-word patterns, the pattern may be
        # split across two lines.  Generate variants where each inter-word
        # boundary is replaced with \\n (plus optional surrounding whitespace).
        words = pattern.text.split()
        if len(words) > 1:
            for i in range(1, len(words)):
                left = " ".join(words[:i])
                right = " ".join(words[i:])
                # Pattern: left + whitespace + newline + optional whitespace + right
                cross_pat = re.escape(left) + r"\s*\n\s*" + re.escape(right)
                lines.append(
                    f"msg = re.sub({cross_pat!r}.encode('utf-8'), replacement, msg, flags={flags})"
                )

    lines.append("return msg")
    return "\n".join(lines)


def _build_blob_callback(patterns: List[Pattern], replacement: str) -> str:
    """Generate Python code for ``git filter-repo --blob-callback`` to
    replace patterns in file contents.

    Respects each pattern's ``case_sensitive`` flag.
    """
    lines: List[str] = []
    lines.append("import re")
    lines.append(f"replacement = {replacement!r}.encode('utf-8')")

    for pattern in patterns:
        flags = _re_flags_for_pattern(pattern)
        lines.append(
            f"blob.data = re.sub({pattern.text!r}.encode('utf-8'), replacement, blob.data, flags={flags})"
        )

    return "\n".join(lines)


# ===================================================================
# Subcommands
# ===================================================================


def cmd_scan(args) -> None:
    """``scan`` subcommand — load patterns, scan commits + file contents,
    print results."""
    patterns_path = args.patterns
    ensure_patterns_file_gitignored(patterns_path)
    case_sensitive = getattr(args, "case_sensitive", False)
    patterns = load_patterns(patterns_path, case_sensitive=case_sensitive)

    if not patterns:
        log("No patterns found in the patterns file", level="WARN")
        return

    log(f"Scanning with {len(patterns)} pattern(s) ...")

    cwd = getattr(args, "repo", None) or None

    # Scan commit messages
    commit_matches = scan_commit_messages(patterns, cwd=cwd)

    # Scan file contents
    blob_matches = scan_file_contents(patterns, cwd=cwd)

    output_json = getattr(args, "json", False)

    if output_json:
        result = {
            "commit_matches": [
                {
                    "sha": m.sha,
                    "subject": m.subject,
                    "line": m.line,
                    "pattern": m.pattern,
                    "line_number": m.line_number,
                }
                for m in commit_matches
            ],
            "blob_matches": [
                {
                    "path": b.path,
                    "line_number": b.line_number,
                    "line": b.line,
                    "pattern": b.pattern,
                }
                for b in blob_matches
            ],
        }
        print(json.dumps(result, indent=2))
        return

    # Text output
    if not commit_matches and not blob_matches:
        log("No matches found", level="OK")
        return

    if commit_matches:
        print(f"\n{'='*60}")
        print(f"COMMIT MESSAGE MATCHES ({len(commit_matches)})")
        print(f"{'='*60}")
        seen_shas: set[str] = set()
        for m in commit_matches:
            if m.sha not in seen_shas:
                seen_shas.add(m.sha)
                print(f"\n  {_short_sha(m.sha)}  {m.subject}")
            print(f"    L{m.line_number}: [{m.pattern}] {m.line.strip()}")

    if blob_matches:
        print(f"\n{'='*60}")
        print(f"FILE CONTENT MATCHES ({len(blob_matches)})")
        print(f"{'='*60}")
        seen_paths: set[str] = set()
        for b in blob_matches:
            if b.path not in seen_paths:
                seen_paths.add(b.path)
                print(f"\n  {b.path}")
            print(f"    L{b.line_number}: [{b.pattern}] {b.line.strip()}")

    # Summary
    unique_commits = len(set(m.sha for m in commit_matches))
    unique_files = len(set(b.path for b in blob_matches))
    print(f"\n{'='*60}")
    print(f"SUMMARY: {len(commit_matches)} commit-message hit(s) in "
          f"{unique_commits} commit(s), "
          f"{len(blob_matches)} file-content hit(s) in {unique_files} file(s)")
    print(f"{'='*60}\n")


def cmd_plan(args) -> None:
    """``plan`` subcommand — scan + build impact report, print summary with
    before/after preview."""
    patterns_path = args.patterns
    ensure_patterns_file_gitignored(patterns_path)
    case_sensitive = getattr(args, "case_sensitive", False)
    patterns = load_patterns(patterns_path, case_sensitive=case_sensitive)

    if not patterns:
        log("No patterns found in the patterns file", level="WARN")
        return

    replacement = args.replacement
    cwd = getattr(args, "repo", None) or None

    log(f"Scanning with {len(patterns)} pattern(s) ...")
    commit_matches = scan_commit_messages(patterns, cwd=cwd)
    blob_matches = scan_file_contents(patterns, cwd=cwd)

    if not commit_matches and not blob_matches:
        log("No matches found — nothing to plan", level="OK")
        return

    log("Building impact plan ...")
    plan = build_plan(commit_matches, replacement, blob_matches=blob_matches, cwd=cwd)

    # Print the plan
    print(f"\n{'='*60}")
    print("IMPACT PLAN")
    print(f"{'='*60}")
    print(f"  Replacement text : {plan.replacement!r}")
    print(f"  Affected commits : {plan.affected_commits}")
    print(f"  Downstream commits (will be rewritten): {plan.downstream_commits}")
    print(f"  Affected tags    : {len(plan.affected_tags)}")
    if plan.affected_tags:
        for tag in plan.affected_tags:
            print(f"    - {tag}")
    print(f"  Affected branches: {len(plan.affected_branches)}")
    if plan.affected_branches:
        for branch in plan.affected_branches:
            print(f"    - {branch}")
    print(f"  Known forks      : {plan.forks}")
    print(f"  File-content hits: {len(plan.blob_matches)}")

    # Before/after preview
    if commit_matches:
        print(f"\n  {'─'*50}")
        print("  BEFORE / AFTER PREVIEW (commit messages)")
        print(f"  {'─'*50}")
        seen: set[str] = set()
        for m in commit_matches[:10]:  # Show first 10
            if m.sha in seen:
                continue
            seen.add(m.sha)
            before = m.line.strip()
            after = before
            for pat in patterns:
                flags = 0 if pat.case_sensitive else re.IGNORECASE
                compiled = re.compile(re.escape(pat.text), flags)
                after = compiled.sub(replacement, after)
            print(f"\n  {_short_sha(m.sha)}")
            print(f"    BEFORE: {before}")
            print(f"    AFTER : {after}")

        remaining = len(set(m.sha for m in commit_matches)) - len(seen)
        if remaining > 0:
            print(f"\n  ... and {remaining} more commit(s)")

    if blob_matches:
        print(f"\n  {'─'*50}")
        print("  BEFORE / AFTER PREVIEW (file contents)")
        print(f"  {'─'*50}")
        shown = 0
        for b in blob_matches:
            if shown >= 10:
                break
            before = b.line.strip()
            after = before
            for pat in patterns:
                flags = 0 if pat.case_sensitive else re.IGNORECASE
                compiled = re.compile(re.escape(pat.text), flags)
                after = compiled.sub(replacement, after)
            print(f"\n  {b.path}:{b.line_number}")
            print(f"    BEFORE: {before}")
            print(f"    AFTER : {after}")
            shown += 1

        remaining_blobs = len(blob_matches) - shown
        if remaining_blobs > 0:
            print(f"\n  ... and {remaining_blobs} more file hit(s)")

    print(f"\n{'='*60}\n")


def cmd_clean(args) -> None:
    """``clean`` subcommand — 7-step pipeline:

    1. Backup via ``git bundle create``
    2. Create work clone via ``git clone --mirror``
    3. Run ``git filter-repo`` with message-callback and blob-callback
    4. Read commit-map
    5. Confirm with user
    6. Toggle branch protection, force push, restore protection
    7. Print old SHAs for GitHub Support ticket
    """
    patterns_path = args.patterns
    ensure_patterns_file_gitignored(patterns_path)
    case_sensitive = getattr(args, "case_sensitive", False)
    patterns = load_patterns(patterns_path, case_sensitive=case_sensitive)

    if not patterns:
        log("No patterns found in the patterns file", level="WARN")
        return

    replacement = args.replacement
    cwd = getattr(args, "repo", None) or os.getcwd()
    cwd = os.path.abspath(cwd)

    # Get remote URL before we do anything
    remote_url = _get_remote_url(cwd=cwd)
    repo = _extract_repo_from_url(remote_url)

    # Step 0: Quick scan to confirm there is work to do
    log("Step 0/7: Quick scan ...")
    commit_matches = scan_commit_messages(patterns, cwd=cwd)
    blob_matches = scan_file_contents(patterns, cwd=cwd)
    history_matches = scan_file_history(patterns, cwd=cwd)

    if not commit_matches and not blob_matches and not history_matches:
        log("No matches found — nothing to clean", level="OK")
        return

    log(f"Found {len(commit_matches)} commit-message hit(s), "
        f"{len(blob_matches)} file-content hit(s), and "
        f"{len(history_matches)} file-history hit(s)")

    plan = build_plan(commit_matches, replacement, blob_matches=blob_matches, cwd=cwd)

    # ── Step 1: Backup ──────────────────────────────────────────────
    log("Step 1/7: Creating backup bundle ...")
    backup_path = os.path.join(cwd, f".git-backup-{os.getpid()}.bundle")
    run_git("bundle", "create", backup_path, "--all", cwd=cwd)
    log(f"Backup saved to {backup_path}", level="OK")

    # ── Step 2: Mirror clone ────────────────────────────────────────
    log("Step 2/7: Creating mirror work-clone ...")
    work_dir = tempfile.mkdtemp(prefix="tidy-mirror-")
    work_clone = os.path.join(work_dir, "repo.git")
    run_git("clone", "--mirror", cwd, work_clone)
    log(f"Mirror clone at {work_clone}", level="OK")

    # ── Step 3: Run git filter-repo ─────────────────────────────────
    log("Step 3/7: Running git filter-repo ...")

    # Check that git-filter-repo is installed
    fr_check = run_cmd(["git", "filter-repo", "--version"], check=False)
    if fr_check.returncode != 0:
        log(
            "git-filter-repo is not installed.  Install it with:\n"
            "    pip install git-filter-repo\n"
            "or see https://github.com/newren/git-filter-repo",
            level="ERROR",
        )
        shutil.rmtree(work_dir, ignore_errors=True)
        sys.exit(1)

    # Write callbacks to temp files
    msg_cb = _build_message_callback(patterns, replacement)
    blob_cb = _build_blob_callback(patterns, replacement)

    msg_cb_path = os.path.join(work_dir, "message_callback.py")
    blob_cb_path = os.path.join(work_dir, "blob_callback.py")

    with open(msg_cb_path, "w") as f:
        f.write(msg_cb)
    with open(blob_cb_path, "w") as f:
        f.write(blob_cb)

    filter_cmd = [
        "git",
        "filter-repo",
        "--message-callback",
        msg_cb,
        "--blob-callback",
        blob_cb,
        "--force",
    ]

    result = run_cmd(filter_cmd, cwd=work_clone, check=False)
    if result.returncode != 0:
        log(f"git filter-repo failed:\n{result.stderr}", level="ERROR")
        shutil.rmtree(work_dir, ignore_errors=True)
        sys.exit(1)

    log("filter-repo completed", level="OK")

    # ── Step 4: Read commit-map ─────────────────────────────────────
    log("Step 4/7: Reading commit map ...")
    commit_map: Dict[str, str] = {}
    map_path = os.path.join(work_clone, "filter-repo", "commit-map")
    if os.path.exists(map_path):
        with open(map_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("old"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    commit_map[parts[0]] = parts[1]
        log(f"Read {len(commit_map)} commit mapping(s)", level="OK")
    else:
        log("No commit-map found (filter-repo may not have rewritten any commits)", level="WARN")

    # ── Step 5: Confirm ─────────────────────────────────────────────
    log("Step 5/7: Confirmation ...")
    print(f"\nAbout to force-push rewritten history to: {repo}")
    print(f"  Commits rewritten : {len(commit_map)}")
    print(f"  Affected branches : {', '.join(plan.affected_branches) or 'all'}")
    print(f"  Affected tags     : {', '.join(plan.affected_tags) or 'none'}")
    print(f"  Known forks       : {plan.forks}")
    if plan.forks > 0:
        print("  WARNING: Forks will NOT be updated automatically.")
        print("  You will need to contact fork owners or file a GitHub Support ticket.")
    print()

    if not getattr(args, "yes", False):
        if not confirm("Proceed with force push?"):
            log("Aborted by user", level="WARN")
            shutil.rmtree(work_dir, ignore_errors=True)
            return

    # ── Step 6: Force push ──────────────────────────────────────────
    log("Step 6/7: Force pushing ...")

    # After filter-repo, remotes are stripped — re-add origin
    run_git("remote", "add", "origin", remote_url, cwd=work_clone, check=False)

    # Toggle branch protection for each affected branch
    saved_protections = []
    for branch in plan.affected_branches:
        try:
            original = disable_force_push_protection(repo, branch)
            if original is not None:
                saved_protections.append(original)
        except Exception as e:
            log(f"Could not disable protection on '{branch}': {e}", level="WARN")

    try:
        # Force push all branches
        push_result = run_git(
            "push", "--force", "--all", "origin",
            cwd=work_clone,
            check=False,
        )
        if push_result.returncode != 0:
            log(f"Force push (branches) failed:\n{push_result.stderr}", level="ERROR")
        else:
            log("Branches force-pushed", level="OK")

        # Force push all tags
        tag_result = run_git(
            "push", "--force", "--tags", "origin",
            cwd=work_clone,
            check=False,
        )
        if tag_result.returncode != 0:
            log(f"Force push (tags) failed:\n{tag_result.stderr}", level="ERROR")
        else:
            log("Tags force-pushed", level="OK")
    finally:
        # Always restore branch protection
        for prot in saved_protections:
            try:
                restore_branch_protection(repo, prot)
            except Exception as e:
                log(f"Could not restore protection on '{prot.branch}': {e}", level="ERROR")

    # ── Step 7: Print old SHAs ──────────────────────────────────────
    log("Step 7/7: Summary ...")

    old_shas = list(commit_map.keys())
    if old_shas:
        print(f"\nOld (now-invalid) SHAs ({len(old_shas)}):")
        for old_sha in old_shas:
            new_sha = commit_map.get(old_sha, "???")
            print(f"  {old_sha} -> {new_sha}")
        print()
        print("Save these old SHAs — you'll need them for the GitHub Support ticket.")
        print("Run `tidy ticket` to generate the ticket text.")
    else:
        print("\nNo commits were rewritten.")

    # Clean up work dir
    shutil.rmtree(work_dir, ignore_errors=True)

    # Update the local repo to match the rewritten remote
    log("Fetching rewritten history into local repo ...")
    run_git("fetch", "origin", cwd=cwd, check=False)

    log("Clean complete!", level="OK")


def cmd_verify(args) -> None:
    """``verify`` subcommand — re-scan local repo and optionally verify via
    the GitHub API."""
    patterns_path = args.patterns
    ensure_patterns_file_gitignored(patterns_path)
    case_sensitive = getattr(args, "case_sensitive", False)
    patterns = load_patterns(patterns_path, case_sensitive=case_sensitive)

    if not patterns:
        log("No patterns found in the patterns file", level="WARN")
        return

    cwd = getattr(args, "repo", None) or None

    # Local scan
    log("Verifying local repository ...")
    commit_matches = scan_commit_messages(patterns, cwd=cwd)
    blob_matches = scan_file_contents(patterns, cwd=cwd)

    if commit_matches:
        log(
            f"FAIL: {len(commit_matches)} commit-message match(es) still present locally",
            level="ERROR",
        )
        for m in commit_matches[:5]:
            print(f"  {_short_sha(m.sha)} L{m.line_number}: [{m.pattern}] {m.line.strip()}")
        if len(commit_matches) > 5:
            print(f"  ... and {len(commit_matches) - 5} more")
    else:
        log("Local commit messages: clean", level="OK")

    if blob_matches:
        log(
            f"FAIL: {len(blob_matches)} file-content match(es) still present locally",
            level="ERROR",
        )
        for b in blob_matches[:5]:
            print(f"  {b.path}:{b.line_number}: [{b.pattern}] {b.line.strip()}")
        if len(blob_matches) > 5:
            print(f"  ... and {len(blob_matches) - 5} more")
    else:
        log("Local file contents: clean", level="OK")

    # Remote verification via GitHub API
    if getattr(args, "remote", False):
        log("Verifying via GitHub API ...")
        try:
            remote_url = _get_remote_url(cwd=cwd)
            repo = _extract_repo_from_url(remote_url)
        except Exception as e:
            log(f"Could not determine remote repo: {e}", level="ERROR")
            return

        # Check each branch
        branches = getattr(args, "branches", None)
        if not branches:
            # Default to common branches
            result = run_git("branch", "-r", "--list", "origin/*", cwd=cwd, check=False)
            if result.returncode == 0:
                branches = []
                for line in result.stdout.strip().splitlines():
                    branch = line.strip().replace("origin/", "", 1)
                    if branch and "->" not in branch:
                        branches.append(branch)
            else:
                branches = ["main"]

        total_checked = 0
        total_failures = 0
        # Convert Pattern objects to strings for GitHub API verification
        pattern_strings = [p.text for p in patterns]
        for branch in branches:
            checked, failures = verify_all_commits_scrubbed(
                repo, branch, pattern_strings, limit=getattr(args, "limit", 100)
            )
            total_checked += checked
            total_failures += failures
            if failures:
                log(f"Branch '{branch}': {failures} failure(s) in {checked} commit(s)", level="ERROR")
            else:
                log(f"Branch '{branch}': {checked} commit(s) clean", level="OK")

        print(f"\nRemote verification: {total_checked} commit(s) checked, "
              f"{total_failures} failure(s)")

    # Final summary
    local_clean = not commit_matches and not blob_matches
    if local_clean:
        log("Verification PASSED", level="OK")
    else:
        log("Verification FAILED — patterns still present", level="ERROR")
        sys.exit(1)


def cmd_ticket(args) -> None:
    """``ticket`` subcommand — generate ready-to-submit GitHub Support ticket
    text."""
    cwd = getattr(args, "repo", None) or None

    try:
        remote_url = _get_remote_url(cwd=cwd)
        repo = _extract_repo_from_url(remote_url)
    except Exception as e:
        log(f"Could not determine remote repo: {e}", level="ERROR")
        sys.exit(1)

    # Get old SHAs from args or from commit-map file
    old_shas: List[str] = []
    if hasattr(args, "shas") and args.shas:
        old_shas = args.shas
    elif hasattr(args, "commit_map") and args.commit_map:
        # Read from a commit-map file
        map_path = args.commit_map
        if os.path.exists(map_path):
            with open(map_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("old"):
                        continue
                    parts = line.split()
                    if parts:
                        old_shas.append(parts[0])
        else:
            log(f"Commit map not found: {map_path}", level="ERROR")
            sys.exit(1)
    else:
        log(
            "Provide old SHAs via --shas or a commit-map file via --commit-map",
            level="ERROR",
        )
        sys.exit(1)

    # Get affected branches
    branches: List[str] = []
    if hasattr(args, "branches") and args.branches:
        branches = args.branches
    else:
        result = run_git("branch", "-r", "--list", "origin/*", cwd=cwd, check=False)
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                branch = line.strip().replace("origin/", "", 1)
                if branch and "->" not in branch:
                    branches.append(branch)

    ticket = generate_ticket_text(repo, old_shas, branches)

    output = getattr(args, "output", None)
    if output:
        with open(output, "w") as f:
            f.write(ticket)
        log(f"Ticket text written to {output}", level="OK")
    else:
        print(ticket)


# ===================================================================
# Multi-repo (org-wide) commands
# ===================================================================


def _list_org_repos(
    org: str,
    *,
    include_private: bool = False,
    skip_forks: bool = True,
) -> List[Tuple[str, str]]:
    """List repos in a GitHub org.  Returns ``[(name, clone_url), ...]``."""
    visibility = "" if include_private else " --visibility public"
    cmd = f"gh repo list {org} --limit 200 --json name,sshUrl,isPrivate,isFork --jq '.[]"
    if skip_forks:
        cmd += " | select(.isFork == false)"
    if not include_private:
        cmd += " | select(.isPrivate == false)"
    cmd += ' | "\\(.name)\\t\\(.sshUrl)"' + "'"
    result = run_cmd(["bash", "-c", cmd], check=False)
    if result.returncode != 0:
        log(f"Failed to list repos for {org}: {result.stderr}", level="ERROR")
        return []
    repos = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            repos.append((parts[0], parts[1]))
    return repos


def _ensure_repo_cloned(
    name: str,
    clone_url: str,
    base_dir: str,
) -> Optional[str]:
    """Ensure *name* is cloned under *base_dir*.  Returns the repo path."""
    repo_path = os.path.join(base_dir, name)
    if os.path.isdir(os.path.join(repo_path, ".git")):
        # Already cloned — fetch latest
        run_git("fetch", "--all", "--prune", cwd=repo_path, check=False)
        return repo_path
    # Clone
    log(f"Cloning {name} ...")
    result = run_git("clone", clone_url, repo_path, check=False)
    if result.returncode != 0:
        log(f"Failed to clone {name}: {result.stderr}", level="ERROR")
        return None
    return repo_path


def cmd_scan_org(args) -> None:
    """``scan-org`` subcommand — scan all (public) repos in a GitHub org."""
    org = args.org
    patterns_path = args.patterns
    ensure_patterns_file_gitignored(patterns_path)
    case_sensitive = getattr(args, "case_sensitive", False)
    patterns = load_patterns(patterns_path, case_sensitive=case_sensitive)

    if not patterns:
        log("No patterns found in the patterns file", level="WARN")
        return

    base_dir = getattr(args, "clone_dir", None)
    if not base_dir:
        base_dir = tempfile.mkdtemp(prefix="tidy-org-")
    base_dir = os.path.abspath(base_dir)

    include_private = getattr(args, "include_private", False)

    log(f"Listing {'all' if include_private else 'public'} repos for {org} ...")
    repos = _list_org_repos(org, include_private=include_private)

    if not repos:
        log("No repos found", level="WARN")
        return

    log(f"Found {len(repos)} repo(s) to scan")

    output_json = getattr(args, "json", False)
    all_results: Dict[str, Dict] = {}
    total_commit_hits = 0
    total_blob_hits = 0
    total_history_hits = 0
    affected_repos: List[str] = []

    for name, clone_url in repos:
        repo_path = _ensure_repo_cloned(name, clone_url, base_dir)
        if repo_path is None:
            continue

        commit_matches = scan_commit_messages(patterns, cwd=repo_path)
        blob_matches = scan_file_contents(patterns, cwd=repo_path)
        history_matches = scan_file_history(patterns, cwd=repo_path)

        if commit_matches or blob_matches or history_matches:
            affected_repos.append(name)
            total_commit_hits += len(commit_matches)
            total_blob_hits += len(blob_matches)
            total_history_hits += len(history_matches)

            if output_json:
                all_results[name] = {
                    "commit_matches": [
                        {"sha": m.sha, "subject": m.subject, "line": m.line,
                         "pattern": m.pattern, "line_number": m.line_number}
                        for m in commit_matches
                    ],
                    "blob_matches": [
                        {"path": b.path, "line_number": b.line_number,
                         "line": b.line, "pattern": b.pattern}
                        for b in blob_matches
                    ],
                    "history_matches": [
                        {"sha": m.sha, "subject": m.subject,
                         "pattern": m.pattern}
                        for m in history_matches
                    ],
                }
            else:
                print(f"\n{'='*60}")
                print(f"  {name}")
                print(f"{'='*60}")

                if commit_matches:
                    seen_shas: set[str] = set()
                    for m in commit_matches:
                        if m.sha not in seen_shas:
                            seen_shas.add(m.sha)
                            print(f"  COMMIT {_short_sha(m.sha)}  {m.subject}")
                        print(f"    L{m.line_number}: [{m.pattern}] {m.line.strip()}")

                if blob_matches:
                    for b in blob_matches:
                        print(f"  FILE {b.path}:{b.line_number}: [{b.pattern}] {b.line.strip()}")

                if history_matches:
                    seen_shas_h: set[str] = set()
                    for m in history_matches:
                        if m.sha not in seen_shas_h:
                            seen_shas_h.add(m.sha)
                            print(f"  HISTORY {_short_sha(m.sha)}  {m.subject}")

    if output_json:
        print(json.dumps(all_results, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"ORG SUMMARY: {len(repos)} repo(s) scanned, "
              f"{len(affected_repos)} affected")
        if affected_repos:
            print(f"  Affected repos: {', '.join(affected_repos)}")
        print(f"  Total commit-message hits : {total_commit_hits}")
        print(f"  Total file-content hits   : {total_blob_hits}")
        print(f"  Total file-history hits   : {total_history_hits}")
        print(f"{'='*60}\n")


def cmd_clean_org(args) -> None:
    """``clean-org`` subcommand — scan + clean all affected repos in a
    GitHub org.

    Iterates over all public repos, scans each, and runs the clean
    pipeline on repos with matches.
    """
    org = args.org
    patterns_path = args.patterns
    ensure_patterns_file_gitignored(patterns_path)
    case_sensitive = getattr(args, "case_sensitive", False)
    patterns = load_patterns(patterns_path, case_sensitive=case_sensitive)

    if not patterns:
        log("No patterns found in the patterns file", level="WARN")
        return

    replacement = args.replacement

    base_dir = getattr(args, "clone_dir", None)
    if not base_dir:
        base_dir = tempfile.mkdtemp(prefix="tidy-org-")
    base_dir = os.path.abspath(base_dir)

    include_private = getattr(args, "include_private", False)
    auto_yes = getattr(args, "yes", False)

    log(f"Listing {'all' if include_private else 'public'} repos for {org} ...")
    repos = _list_org_repos(org, include_private=include_private)

    if not repos:
        log("No repos found", level="WARN")
        return

    log(f"Found {len(repos)} repo(s) — scanning for matches ...")

    affected: List[Tuple[str, str, List[Match], List[BlobMatch]]] = []

    for name, clone_url in repos:
        repo_path = _ensure_repo_cloned(name, clone_url, base_dir)
        if repo_path is None:
            continue

        commit_matches = scan_commit_messages(patterns, cwd=repo_path)
        blob_matches = scan_file_contents(patterns, cwd=repo_path)
        history_matches = scan_file_history(patterns, cwd=repo_path)

        if commit_matches or blob_matches or history_matches:
            affected.append((name, repo_path, commit_matches, blob_matches))
            log(f"{name}: {len(commit_matches)} commit + {len(blob_matches)} file + "
                f"{len(history_matches)} history hit(s)")
        else:
            log(f"{name}: clean", level="OK")

    if not affected:
        log("All repos clean — nothing to do", level="OK")
        return

    # Summary before proceeding
    print(f"\n{'='*60}")
    print(f"REPOS TO CLEAN ({len(affected)}):")
    for name, _, cm, bm in affected:
        print(f"  {name}: {len(cm)} commit-message + {len(bm)} file-content hit(s)")
    print(f"{'='*60}\n")

    if not auto_yes:
        if not confirm(f"Proceed to clean {len(affected)} repo(s)?"):
            log("Aborted by user", level="WARN")
            return

    # Clean each affected repo
    cleaned = 0
    failed = 0
    for name, repo_path, _, _ in affected:
        log(f"\n--- Cleaning {name} ---")

        try:
            remote_url = _get_remote_url(cwd=repo_path)
            repo_slug = _extract_repo_from_url(remote_url)
        except Exception as e:
            log(f"Could not determine remote for {name}: {e}", level="ERROR")
            failed += 1
            continue

        # Re-scan (in case state changed)
        cm = scan_commit_messages(patterns, cwd=repo_path)
        bm = scan_file_contents(patterns, cwd=repo_path)

        if not cm and not bm:
            log(f"{name}: no matches on re-scan — skipping", level="OK")
            continue

        plan = build_plan(cm, replacement, blob_matches=bm, cwd=repo_path)

        # Backup
        backup_path = os.path.join(repo_path, f".git-backup-{os.getpid()}.bundle")
        run_git("bundle", "create", backup_path, "--all", cwd=repo_path)
        log(f"Backup: {backup_path}", level="OK")

        # Mirror clone
        work_dir = tempfile.mkdtemp(prefix=f"tidy-{name}-")
        work_clone = os.path.join(work_dir, "repo.git")
        run_git("clone", "--mirror", repo_path, work_clone)

        # Run git filter-repo
        fr_check = run_cmd(["git", "filter-repo", "--version"], check=False)
        if fr_check.returncode != 0:
            log("git-filter-repo not installed", level="ERROR")
            shutil.rmtree(work_dir, ignore_errors=True)
            failed += 1
            continue

        msg_cb = _build_message_callback(patterns, replacement)
        blob_cb = _build_blob_callback(patterns, replacement)

        filter_cmd = [
            "git", "filter-repo",
            "--message-callback", msg_cb,
            "--blob-callback", blob_cb,
            "--force",
        ]
        result = run_cmd(filter_cmd, cwd=work_clone, check=False)
        if result.returncode != 0:
            log(f"filter-repo failed for {name}: {result.stderr}", level="ERROR")
            shutil.rmtree(work_dir, ignore_errors=True)
            failed += 1
            continue

        # Re-add remote and force push
        run_git("remote", "add", "origin", remote_url, cwd=work_clone, check=False)

        # Toggle branch protection
        saved_protections = []
        for branch in plan.affected_branches:
            try:
                original = disable_force_push_protection(repo_slug, branch)
                if original is not None:
                    saved_protections.append(original)
            except Exception as e:
                log(f"Could not disable protection on '{branch}': {e}", level="WARN")

        try:
            push_result = run_git(
                "push", "--force", "--all", "origin",
                cwd=work_clone, check=False,
            )
            if push_result.returncode != 0:
                log(f"Force push (branches) failed for {name}: {push_result.stderr}", level="ERROR")
                failed += 1
            else:
                tag_result = run_git(
                    "push", "--force", "--tags", "origin",
                    cwd=work_clone, check=False,
                )
                if tag_result.returncode != 0:
                    log(f"Force push (tags) failed for {name}: {tag_result.stderr}", level="WARN")
                log(f"{name}: cleaned successfully", level="OK")
                cleaned += 1
        finally:
            for prot in saved_protections:
                try:
                    restore_branch_protection(repo_slug, prot)
                except Exception as e:
                    log(f"Could not restore protection: {e}", level="ERROR")

        # Clean up
        shutil.rmtree(work_dir, ignore_errors=True)

        # Fetch rewritten history into local repo
        run_git("fetch", "origin", cwd=repo_path, check=False)

    # Final summary
    print(f"\n{'='*60}")
    print(f"ORG CLEAN SUMMARY: {cleaned} cleaned, {failed} failed, "
          f"{len(affected) - cleaned - failed} skipped")
    print(f"{'='*60}\n")

# tidy — Git History & Build Artifact Scrubbing Tool

A CLI tool for scanning, planning, executing, verifying, and reporting
git-history rewrites to remove sensitive patterns from commit messages,
file contents, and **build artifacts** (GitHub Releases, GitHub Actions,
PyPI packages, and Docker/GHCR container images).

## Prerequisites

- Python 3.10+
- `git-filter-repo` (install via `pip install git-filter-repo`)
- `gh` CLI (for GitHub API operations: fork enumeration, branch protection, verification, artifact scanning)
- `curl` (for PyPI package downloads)

## Setup

1. Create a patterns file at `tidy/patterns` with one sensitive
   string per line.  Lines starting with `#` are comments.

   Per-pattern case-sensitivity can be controlled with prefixes:
   - `cs:Acme Corp` — force case-sensitive matching
   - `ci:acme` — force case-insensitive matching
   - `Acme Corp` — uses the default (case-insensitive unless `--case-sensitive`)

2. Ensure `tidy/patterns` is listed in `.gitignore` (the tool
   enforces this as a safety check).

## Subcommands

### scan

Scan commit messages and tracked file contents for pattern matches.

```
python -m tidy scan --patterns tidy/patterns [--repo /path/to/repo] [--json]
```

- `--patterns` (required): path to the patterns file.
- `--repo`: path to the git repository (defaults to current directory).
- `--json`: output results as JSON instead of human-readable text.

### plan

Scan + build an impact report showing affected commits, downstream commits,
tags, branches, forks, and a before/after preview of what the replacement
will look like.

```
python -m tidy plan --patterns tidy/patterns --replacement "[REDACTED]" [--repo /path/to/repo]
```

- `--replacement` (default `[REDACTED]`): the text that replaces each
  matched pattern.

### clean

Execute the full scrub pipeline:

1. **Backup** — creates a git bundle of the entire repo.
2. **Mirror clone** — works on a temporary `--mirror` clone.
3. **filter-repo** — runs `git filter-repo` with both `--message-callback`
   (commit messages) and `--blob-callback` (file contents).
4. **Commit map** — reads the old-SHA to new-SHA mapping.
5. **Confirm** — prompts the user before force-pushing (skip with `--yes`).
6. **Force push** — disables branch protection, force-pushes all branches
   and tags, then restores protection (in a `try/finally`).
7. **Summary** — prints old SHAs needed for a GitHub Support ticket.

```
python -m tidy clean --patterns tidy/patterns --replacement "[REDACTED]" [--repo /path/to/repo] [--yes]
```

- `--yes` / `-y`: skip the confirmation prompt.

**Important notes:**

- After `filter-repo`, remotes are stripped.  The tool re-adds `origin`
  before pushing.
- Uses `git push --force --all` then `git push --force --tags` (not
  `--mirror`).
- Branch protection is disabled then restored in a `try/finally` to avoid
  leaving the repo unprotected on error.

### verify

Re-scan the local repository to confirm patterns are gone.  Optionally
verify via the GitHub API.

```
python -m tidy verify --patterns tidy/patterns [--remote] [--branches main develop] [--limit 200]
```

- `--remote`: also check commits via the GitHub API.
- `--branches`: which remote branches to check (default: all).
- `--limit`: max commits per branch to check via API (default: 100).

### ticket

Generate a ready-to-submit GitHub Support ticket requesting cache/ghost
commit purge.

```
python -m tidy ticket --commit-map /path/to/filter-repo/commit-map [--output ticket.txt]
python -m tidy ticket --shas abc123 def456 [--branches main] [--output ticket.txt]
```

- `--shas`: provide old SHAs directly on the command line.
- `--commit-map`: path to the `commit-map` file produced by `git filter-repo`.
- `--branches`: affected branch names (default: auto-detect).
- `--output` / `-o`: write to file instead of stdout.

### scan-org

Scan all public repos in a GitHub organization for sensitive patterns.
Clones each repo into a temp directory (or reuses existing clones) and
runs the same commit/file scan as `scan`.

```
python -m tidy scan-org --org OpenAdaptAI --patterns tidy/patterns [--clone-dir /tmp/repos] [--include-private] [--json]
```

- `--org` (required): GitHub organization name.
- `--clone-dir`: directory to clone repos into (default: temp dir).
- `--include-private`: also scan private repos (default: public only).
- `--json`: output results as JSON.

### clean-org

Scan + clean all affected repos in a GitHub organization.  Runs the full
`clean` pipeline on each repo that contains matches.

```
python -m tidy clean-org --org OpenAdaptAI --patterns tidy/patterns --replacement "[REDACTED]" [--clone-dir /tmp/repos] [--yes]
```

- `--org` (required): GitHub organization name.
- `--include-private`: also clean private repos.
- `--yes` / `-y`: skip confirmation prompt.

### scan-artifacts

Scan build artifacts across GitHub Releases, GitHub Actions, PyPI, and
Docker/GHCR for sensitive patterns.  This is a **read-only** operation
that downloads and inspects artifacts without modifying anything.

```
python -m tidy scan-artifacts --patterns tidy/patterns --repo owner/repo --types all [--json]
python -m tidy scan-artifacts --patterns tidy/patterns --repo owner/repo --types releases actions
python -m tidy scan-artifacts --patterns tidy/patterns --repo owner/repo --types pypi --package my-package
```

- `--repo` (required): GitHub repo in `owner/repo` format.
- `--types`: artifact types to scan (default: `all`).
  Options: `releases`, `actions`, `pypi`, `docker`, `all`.
- `--package`: PyPI package name (default: inferred from repo name).
- `--max-runs`: max workflow runs to scan logs for (default: 100).
- `--max-artifacts`: max workflow artifacts to scan (default: 200).
- `--json`: output results as JSON (to stdout).

**What each scanner checks:**

| Type | What it scans |
|------|---------------|
| `releases` | Release body text (markdown) + downloaded release assets (text files, zip/tar/whl archives) |
| `actions` | Workflow artifact zips + workflow run log zips |
| `pypi` | All published wheels and sdists — downloads and extracts each |
| `docker` | Container package version tags and metadata; flags images built from affected commit SHAs |

### clean-artifacts

Scan and clean build artifacts containing sensitive patterns.  Runs in
**dry-run mode** by default — pass `--confirm` to actually delete/redact.

```
# Dry-run (report only, no changes)
python -m tidy clean-artifacts --patterns tidy/patterns --repo owner/repo --types all

# Actually clean (with confirmation prompt)
python -m tidy clean-artifacts --patterns tidy/patterns --repo owner/repo --types releases --confirm

# Actually clean (skip prompt)
python -m tidy clean-artifacts --patterns tidy/patterns --repo owner/repo --types all --confirm --yes
```

- `--repo` (required): GitHub repo in `owner/repo` format.
- `--types`: artifact types to clean (default: `all`).
- `--confirm`: actually perform deletions/redactions.  Without this flag
  only a dry-run report is generated.
- `--yes` / `-y`: skip the confirmation prompt (still requires `--confirm`).
- `--replacement`: replacement text for redaction (default: `[REDACTED]`).

**Cleaning actions by type:**

| Type | Action |
|------|--------|
| `releases` | Redacts release body text (PATCH) + deletes contaminated assets |
| `actions` | Deletes workflow artifacts + deletes workflow run logs |
| `pypi` | Yanks affected versions (requires `PYPI_API_TOKEN` env var). Notes: yanking hides from default `pip install` but the version remains downloadable with explicit pinning. For full deletion after 72 hours, contact `security@pypi.org`. |
| `docker` | Deletes container package versions by ID |

## Typical Workflow

### Git history scrubbing

```bash
# 1. Scan for matches
python -m tidy scan --patterns tidy/patterns

# 2. Review the impact plan
python -m tidy plan --patterns tidy/patterns --replacement "[REDACTED]"

# 3. Execute the clean (creates backup, rewrites, force-pushes)
python -m tidy clean --patterns tidy/patterns --replacement "[REDACTED]"

# 4. Verify locally and remotely
python -m tidy verify --patterns tidy/patterns --remote

# 5. Generate GitHub Support ticket for cache purge
python -m tidy ticket --commit-map /tmp/tidy-mirror-*/repo.git/filter-repo/commit-map -o ticket.txt
```

### Organization-wide scan

```bash
# Scan all public repos in the org
python -m tidy scan-org --org OpenAdaptAI --patterns tidy/patterns

# Clean all affected repos
python -m tidy clean-org --org OpenAdaptAI --patterns tidy/patterns --replacement "[REDACTED]"
```

### Build artifact scanning

After scrubbing git history, you should also check build artifacts that
may have been produced from the pre-scrub commits:

```bash
# 6. Scan all artifact types for a repo
python -m tidy scan-artifacts --patterns tidy/patterns --repo OpenAdaptAI/openadapt-evals --types all

# 7. Dry-run clean (see what would be deleted)
python -m tidy clean-artifacts --patterns tidy/patterns --repo OpenAdaptAI/openadapt-evals --types all

# 8. Actually clean (with confirmation)
python -m tidy clean-artifacts --patterns tidy/patterns --repo OpenAdaptAI/openadapt-evals --types all --confirm

# Scan specific artifact types only
python -m tidy scan-artifacts --patterns tidy/patterns --repo OpenAdaptAI/openadapt-ml --types releases actions

# Scan PyPI with explicit package name
python -m tidy scan-artifacts --patterns tidy/patterns --repo OpenAdaptAI/openadapt --types pypi --package openadapt
```

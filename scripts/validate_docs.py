"""Validate the generated docs site."""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"


def check_empty_pages(docs_dir=None):
    """Check for empty or stub-only doc pages."""
    docs_dir = pathlib.Path(docs_dir or DOCS_DIR)
    issues = []
    for md_file in docs_dir.rglob("*.md"):
        content = md_file.read_text().strip()
        if len(content) < 20:
            issues.append(f"Nearly empty page: {md_file.relative_to(docs_dir)}")
    return issues


def run_mkdocs_build(strict=True):
    """Run mkdocs build and return (success, output)."""
    cmd = ["mkdocs", "build"]
    if strict:
        cmd.append("--strict")
    cmd.extend(["--site-dir", str(ROOT / "_site_check")])
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    # Clean up
    site_dir = ROOT / "_site_check"
    if site_dir.exists():
        import shutil
        shutil.rmtree(site_dir)
    return result.returncode == 0, result.stdout + result.stderr


def validate():
    """Run all validation checks. Returns list of issues."""
    issues = []

    # Check for empty pages
    empty_issues = check_empty_pages()
    issues.extend(empty_issues)

    # Run mkdocs build
    success, output = run_mkdocs_build()
    if not success:
        issues.append(f"mkdocs build --strict failed:\n{output}")

    if issues:
        print(f"Validation found {len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("Validation passed!")

    return issues


if __name__ == "__main__":
    issues = validate()
    sys.exit(1 if issues else 0)

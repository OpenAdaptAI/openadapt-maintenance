"""Tests for sync_readmes.py"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from sync_readmes import sync, load_repos, fetch_readme

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_load_repos():
    repos = load_repos()
    assert len(repos) > 0
    assert all("name" in r and "github" in r for r in repos)


def test_sync_renders_pages(tmp_path, mocker):
    """Sync should render a page for each repo using the template."""
    sample_readme = (FIXTURES / "sample_readme.md").read_text()
    mocker.patch("sync_readmes.fetch_readme", return_value=sample_readme)

    repos = [
        {"name": "test-pkg", "github": "OpenAdaptAI/test-pkg",
         "doc_page": "packages/test-pkg.md", "category": "core"},
    ]
    results = sync(repos=repos, docs_dir=tmp_path, templates_dir=ROOT / "templates")

    assert len(results) == 1
    out_file = tmp_path / "packages" / "test-pkg.md"
    assert out_file.exists()
    content = out_file.read_text()
    assert "test-pkg" in content
    assert "openadapt-example" in content  # from sample README
    assert "Auto-generated" in content


def test_sync_creates_directories(tmp_path, mocker):
    """Sync should create the packages/ directory if it doesn't exist."""
    mocker.patch("sync_readmes.fetch_readme", return_value="# Test")
    repos = [
        {"name": "deep-pkg", "github": "OpenAdaptAI/deep-pkg",
         "doc_page": "nested/deep/deep-pkg.md", "category": "core"},
    ]
    sync(repos=repos, docs_dir=tmp_path, templates_dir=ROOT / "templates")
    assert (tmp_path / "nested" / "deep" / "deep-pkg.md").exists()


def test_fetch_readme_handles_failure(mocker):
    """fetch_readme should return a fallback message on HTTP error."""
    mock_resp = mocker.Mock()
    mock_resp.status_code = 404
    mocker.patch("sync_readmes.requests.get", return_value=mock_resp)
    result = fetch_readme("OpenAdaptAI/nonexistent")
    assert "not available" in result


def test_sync_all_repos_artifact(tmp_path, mocker):
    """Generate artifacts for all configured repos (mocked)."""
    sample_readme = (FIXTURES / "sample_readme.md").read_text()
    mocker.patch("sync_readmes.fetch_readme", return_value=sample_readme)

    repos = load_repos()
    results = sync(repos=repos, docs_dir=tmp_path, templates_dir=ROOT / "templates")

    # Write artifact list
    artifacts_dir = ROOT / "tests" / "artifacts" / "generated_docs"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for r in results:
        rel_path = pathlib.Path(r["path"]).relative_to(tmp_path)
        # Copy to artifacts
        dest = artifacts_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((tmp_path / rel_path).read_text())
        manifest.append(str(rel_path))

    (artifacts_dir / "MANIFEST.txt").write_text("\n".join(manifest))
    assert len(results) == len(repos)

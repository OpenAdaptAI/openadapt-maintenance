"""Tests for aggregate_changelog.py"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from aggregate_changelog import aggregate, fetch_releases


def test_aggregate_writes_changelog(tmp_path, mocker):
    """Aggregate should write a changelog.md file."""
    mock_releases = [
        {"tag_name": "v0.3.0", "published_at": "2026-02-15T00:00:00Z",
         "html_url": "https://github.com/OpenAdaptAI/test/releases/v0.3.0",
         "body": "Added new CLI command", "draft": False},
        {"tag_name": "v0.2.0", "published_at": "2026-01-10T00:00:00Z",
         "html_url": "https://github.com/OpenAdaptAI/test/releases/v0.2.0",
         "body": "Fixed import error", "draft": False},
    ]
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_releases
    mocker.patch("aggregate_changelog.requests.get", return_value=mock_resp)

    repos = [
        {"name": "test-pkg", "github": "OpenAdaptAI/test-pkg", "changelog": True},
    ]
    result = aggregate(repos=repos, docs_dir=tmp_path)
    content = (tmp_path / "changelog.md").read_text()

    assert "test-pkg" in content
    assert "v0.3.0" in content
    assert "v0.2.0" in content
    assert "Added new CLI" in content


def test_aggregate_skips_drafts(tmp_path, mocker):
    """Draft releases should be skipped."""
    mock_releases = [
        {"tag_name": "v0.4.0-rc", "published_at": "2026-03-01T00:00:00Z",
         "html_url": "https://example.com", "body": "Draft", "draft": True},
        {"tag_name": "v0.3.0", "published_at": "2026-02-15T00:00:00Z",
         "html_url": "https://example.com", "body": "Released", "draft": False},
    ]
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_releases
    mocker.patch("aggregate_changelog.requests.get", return_value=mock_resp)

    repos = [{"name": "pkg", "github": "OpenAdaptAI/pkg", "changelog": True}]
    aggregate(repos=repos, docs_dir=tmp_path)
    content = (tmp_path / "changelog.md").read_text()

    assert "v0.3.0" in content
    assert "v0.4.0-rc" not in content


def test_aggregate_handles_no_releases(tmp_path, mocker):
    """Should still write a file even if repos have no releases."""
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    mocker.patch("aggregate_changelog.requests.get", return_value=mock_resp)

    repos = [{"name": "empty", "github": "OpenAdaptAI/empty", "changelog": True}]
    aggregate(repos=repos, docs_dir=tmp_path)
    assert (tmp_path / "changelog.md").exists()


def test_fetch_releases_handles_http_error(mocker):
    mock_resp = mocker.Mock()
    mock_resp.status_code = 500
    mocker.patch("aggregate_changelog.requests.get", return_value=mock_resp)
    result = fetch_releases("OpenAdaptAI/broken")
    assert result == []

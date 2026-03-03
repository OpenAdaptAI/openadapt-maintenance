"""Tests for validate_docs.py"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from validate_docs import check_empty_pages


def test_check_empty_pages_finds_issues(tmp_path):
    """Should flag pages with less than 20 chars."""
    (tmp_path / "short.md").write_text("# Hi")
    (tmp_path / "ok.md").write_text("# This is a proper page\n\nWith enough content to pass.")

    issues = check_empty_pages(docs_dir=tmp_path)
    assert len(issues) == 1
    assert "short.md" in issues[0]


def test_check_empty_pages_no_issues(tmp_path):
    """Should return empty list when all pages are adequate."""
    (tmp_path / "good.md").write_text("# Good Page\n\nThis has enough content to be useful.")
    issues = check_empty_pages(docs_dir=tmp_path)
    assert len(issues) == 0


def test_check_empty_pages_nested(tmp_path):
    """Should recurse into subdirectories."""
    sub = tmp_path / "packages"
    sub.mkdir()
    (sub / "tiny.md").write_text("")
    (sub / "fine.md").write_text("# Fine package\n\nLots of good documentation here.")

    issues = check_empty_pages(docs_dir=tmp_path)
    assert len(issues) == 1
    assert "tiny.md" in issues[0]

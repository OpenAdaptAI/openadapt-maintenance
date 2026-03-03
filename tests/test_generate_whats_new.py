"""Tests for generate_whats_new.py"""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from generate_whats_new import generate, llm_summarize

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def test_generate_without_llm(tmp_path, mocker, monkeypatch):
    """Without API key, should produce a raw PR list page."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    sample_prs = json.loads((FIXTURES / "sample_prs.json").read_text())
    mocker.patch("generate_whats_new.fetch_merged_prs", return_value=sample_prs)

    repos = [
        {"name": "openadapt-evals", "github": "OpenAdaptAI/openadapt-evals"},
    ]
    result = generate(repos=repos, docs_dir=tmp_path, days=7)

    content = (tmp_path / "whats-new.md").read_text()
    assert "What's New" in content
    assert "openadapt-evals" in content
    assert "evaluation pipeline" in content


def test_generate_with_no_prs(tmp_path, mocker, monkeypatch):
    """Should handle case where no PRs are found."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    mocker.patch("generate_whats_new.fetch_merged_prs", return_value=[])

    repos = [{"name": "empty-repo", "github": "OpenAdaptAI/empty-repo"}]
    generate(repos=repos, docs_dir=tmp_path, days=7)

    content = (tmp_path / "whats-new.md").read_text()
    assert "No merged PRs" in content or "What's New" in content


def test_llm_summarize_no_api_key(monkeypatch):
    """LLM summary should return None without API key."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = llm_summarize("some prs", 7)
    assert result is None


def test_llm_summarize_no_anthropic_package(monkeypatch, mocker):
    """LLM summary should return None if anthropic package not installed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    mocker.patch.dict("sys.modules", {"anthropic": None})

    # Force reimport to pick up the mocked module
    import importlib
    import generate_whats_new
    importlib.reload(generate_whats_new)

    result = generate_whats_new.llm_summarize("some prs", 7)
    assert result is None


def test_generate_writes_artifact(tmp_path, mocker, monkeypatch):
    """Generate a sample What's New page as a test artifact."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    sample_prs = json.loads((FIXTURES / "sample_prs.json").read_text())
    mocker.patch("generate_whats_new.fetch_merged_prs", return_value=sample_prs)

    repos = [
        {"name": "openadapt-evals", "github": "OpenAdaptAI/openadapt-evals"},
        {"name": "openadapt-ml", "github": "OpenAdaptAI/openadapt-ml"},
    ]
    generate(repos=repos, docs_dir=tmp_path, days=7)

    # Copy to artifacts
    root = pathlib.Path(__file__).resolve().parent.parent
    artifacts_dir = root / "tests" / "artifacts" / "generated_docs"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "whats-new.md"
    dest = artifacts_dir / "whats-new.md"
    dest.write_text(src.read_text())

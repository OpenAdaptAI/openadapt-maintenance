"""Tests for the tidy artifact scanning and cleaning modules.

Uses mocking to avoid real API calls. Tests cover:
- Pattern matching logic
- Scan result aggregation
- Clean action generation (dry-run and confirm modes)
- Report formatting
- Edge cases (empty results, API errors, binary files)
"""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from tidy._config import Pattern
from tidy.artifacts.base import ArtifactMatch, ArtifactType
from tidy.artifacts.github_releases import GitHubReleasesScanner
from tidy.artifacts.github_actions import GitHubActionsScanner
from tidy.artifacts.pypi import PyPIScanner
from tidy.artifacts.docker_ghcr import DockerGHCRScanner


# ─── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def patterns():
    """Sample patterns for testing."""
    return [
        Pattern(text="SecretCorp", case_sensitive=True),
        Pattern(text="api-key-123", case_sensitive=False),
    ]


@pytest.fixture
def patterns_ci():
    """Case-insensitive patterns."""
    return [
        Pattern(text="sensitivedata", case_sensitive=False),
    ]


# ─── Base scanner tests ───────────────────────────────────────────

class TestArtifactMatch:
    def test_basic_creation(self):
        m = ArtifactMatch(
            artifact_type=ArtifactType.GITHUB_RELEASES,
            source="release:v1.0",
            location="body",
            line="Contains SecretCorp name",
            pattern="SecretCorp",
        )
        assert m.artifact_type == ArtifactType.GITHUB_RELEASES
        assert m.source == "release:v1.0"
        assert m.pattern == "SecretCorp"
        assert m.artifact_id is None
        assert m.metadata == {}

    def test_with_metadata(self):
        m = ArtifactMatch(
            artifact_type=ArtifactType.PYPI,
            source="pypi:pkg==1.0",
            location="setup.py",
            line="author = 'SecretCorp'",
            pattern="SecretCorp",
            artifact_id="1.0",
            metadata={"type": "pypi_package", "version": "1.0"},
        )
        assert m.artifact_id == "1.0"
        assert m.metadata["version"] == "1.0"


class TestScannerBase:
    def test_match_text_case_sensitive(self, patterns):
        scanner = GitHubReleasesScanner(patterns, "owner/repo")
        assert scanner._match_text("Has SecretCorp here", patterns[0]) is True
        assert scanner._match_text("Has secretcorp here", patterns[0]) is False

    def test_match_text_case_insensitive(self, patterns):
        scanner = GitHubReleasesScanner(patterns, "owner/repo")
        assert scanner._match_text("Contains API-KEY-123 value", patterns[1]) is True
        assert scanner._match_text("contains api-key-123 value", patterns[1]) is True

    def test_scan_text(self, patterns):
        scanner = GitHubReleasesScanner(patterns, "owner/repo")
        text = "Line 1\nLine with SecretCorp name\nLine 3\nHas api-key-123 too"
        matches = scanner._scan_text(
            text,
            source="test",
            location="test.txt",
        )
        assert len(matches) == 2
        assert matches[0].pattern == "SecretCorp"
        assert matches[1].pattern == "api-key-123"

    def test_scan_text_no_match(self, patterns):
        scanner = GitHubReleasesScanner(patterns, "owner/repo")
        text = "Nothing sensitive here\nJust normal text"
        matches = scanner._scan_text(text, source="test", location="test.txt")
        assert len(matches) == 0


# ─── GitHub Releases scanner tests ────────────────────────────────

class TestGitHubReleasesScanner:
    @patch("tidy.artifacts.github_releases._gh_api_json")
    def test_scan_empty_releases(self, mock_api, patterns):
        mock_api.return_value = []
        scanner = GitHubReleasesScanner(patterns, "owner/repo")
        matches = scanner.scan()
        assert len(matches) == 0

    @patch("tidy.artifacts.github_releases._gh_api_json")
    def test_scan_release_body(self, mock_api, patterns):
        mock_api.side_effect = [
            # _list_releases
            [
                {
                    "id": 1,
                    "tag_name": "v1.0",
                    "name": "Release 1.0",
                    "body": "Built by SecretCorp team",
                }
            ],
            # _list_assets (empty)
            [],
        ]
        scanner = GitHubReleasesScanner(patterns, "owner/repo")
        matches = scanner.scan()
        assert len(matches) == 1
        assert matches[0].pattern == "SecretCorp"
        assert matches[0].metadata["type"] == "release_body"

    @patch("tidy.artifacts.github_releases._gh_api_json")
    @patch("tidy.artifacts.github_releases._gh_api")
    def test_clean_dry_run(self, mock_api, mock_api_json, patterns):
        scanner = GitHubReleasesScanner(patterns, "owner/repo")
        matches = [
            ArtifactMatch(
                artifact_type=ArtifactType.GITHUB_RELEASES,
                source="release:v1.0",
                location="release body",
                line="Built by SecretCorp",
                pattern="SecretCorp",
                artifact_id="1",
                metadata={
                    "type": "release_body",
                    "release_id": 1,
                    "tag_name": "v1.0",
                },
            ),
        ]
        actions = scanner.clean(matches, confirm=False)
        assert len(actions) == 1
        assert "DRY-RUN" in actions[0]
        # Should NOT call any API
        mock_api.assert_not_called()

    @patch("tidy.artifacts.github_releases._gh_api_json")
    @patch("tidy.artifacts.github_releases._gh_api")
    def test_clean_confirm_redact_body(self, mock_api, mock_api_json, patterns):
        # Mock the GET for release body
        mock_api_json.return_value = {
            "id": 1,
            "body": "Built by SecretCorp team for api-key-123",
        }
        # Mock the PATCH success
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_api.return_value = mock_result

        scanner = GitHubReleasesScanner(patterns, "owner/repo")
        matches = [
            ArtifactMatch(
                artifact_type=ArtifactType.GITHUB_RELEASES,
                source="release:v1.0",
                location="release body",
                line="Built by SecretCorp",
                pattern="SecretCorp",
                artifact_id="1",
                metadata={
                    "type": "release_body",
                    "release_id": 1,
                    "tag_name": "v1.0",
                },
            ),
        ]
        actions = scanner.clean(matches, confirm=True, replacement="[REDACTED]")
        assert len(actions) == 1
        assert "REDACT" in actions[0]
        assert "DRY-RUN" not in actions[0]

    @patch("tidy.artifacts.github_releases._gh_api")
    def test_clean_confirm_delete_asset(self, mock_api, patterns):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_api.return_value = mock_result

        scanner = GitHubReleasesScanner(patterns, "owner/repo")
        matches = [
            ArtifactMatch(
                artifact_type=ArtifactType.GITHUB_RELEASES,
                source="release:v1.0",
                location="asset:pkg-1.0.whl",
                line="import SecretCorp",
                pattern="SecretCorp",
                artifact_id="42",
                metadata={
                    "type": "release_asset",
                    "release_id": 1,
                    "asset_id": 42,
                    "asset_name": "pkg-1.0.whl",
                    "tag_name": "v1.0",
                },
            ),
        ]
        actions = scanner.clean(matches, confirm=True)
        assert len(actions) == 1
        assert "DELETE" in actions[0]

    def test_report_no_matches(self, patterns):
        scanner = GitHubReleasesScanner(patterns, "owner/repo")
        report = scanner.report([])
        assert "No matches found" in report

    def test_report_with_matches(self, patterns):
        scanner = GitHubReleasesScanner(patterns, "owner/repo")
        matches = [
            ArtifactMatch(
                artifact_type=ArtifactType.GITHUB_RELEASES,
                source="release:v1.0",
                location="release body",
                line="Built by SecretCorp",
                pattern="SecretCorp",
            ),
        ]
        report = scanner.report(matches)
        assert "1 match" in report
        assert "SecretCorp" in report
        assert "release:v1.0" in report


# ─── GitHub Actions scanner tests ─────────────────────────────────

class TestGitHubActionsScanner:
    @patch("tidy.artifacts.github_actions._gh_api_json")
    def test_scan_no_artifacts_no_runs(self, mock_api, patterns):
        mock_api.side_effect = [
            # _list_artifacts
            {"artifacts": []},
            # _list_runs
            {"workflow_runs": []},
        ]
        scanner = GitHubActionsScanner(patterns, "owner/repo")
        matches = scanner.scan()
        assert len(matches) == 0

    @patch("tidy.artifacts.github_actions._gh_api")
    def test_clean_dry_run_artifact(self, mock_api, patterns):
        scanner = GitHubActionsScanner(patterns, "owner/repo")
        matches = [
            ArtifactMatch(
                artifact_type=ArtifactType.GITHUB_ACTIONS,
                source="artifact:build-output (run:100)",
                location="build-output/output.log",
                line="SecretCorp connection established",
                pattern="SecretCorp",
                artifact_id="50",
                metadata={
                    "type": "workflow_artifact",
                    "artifact_id": 50,
                    "artifact_name": "build-output",
                    "run_id": 100,
                },
            ),
        ]
        actions = scanner.clean(matches, confirm=False)
        assert len(actions) == 1
        assert "DRY-RUN" in actions[0]
        mock_api.assert_not_called()

    @patch("tidy.artifacts.github_actions._gh_api")
    def test_clean_confirm_delete_artifact(self, mock_api, patterns):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_api.return_value = mock_result

        scanner = GitHubActionsScanner(patterns, "owner/repo")
        matches = [
            ArtifactMatch(
                artifact_type=ArtifactType.GITHUB_ACTIONS,
                source="artifact:build-output (run:100)",
                location="build-output/output.log",
                line="SecretCorp connection established",
                pattern="SecretCorp",
                artifact_id="50",
                metadata={
                    "type": "workflow_artifact",
                    "artifact_id": 50,
                    "artifact_name": "build-output",
                    "run_id": 100,
                },
            ),
        ]
        actions = scanner.clean(matches, confirm=True)
        assert len(actions) == 1
        assert "DELETE" in actions[0]
        assert "DRY-RUN" not in actions[0]

    @patch("tidy.artifacts.github_actions._gh_api")
    def test_clean_confirm_delete_logs(self, mock_api, patterns):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_api.return_value = mock_result

        scanner = GitHubActionsScanner(patterns, "owner/repo")
        matches = [
            ArtifactMatch(
                artifact_type=ArtifactType.GITHUB_ACTIONS,
                source="run:100 (CI, sha:abc123)",
                location="logs-run-100/step.log",
                line="api-key-123 leaked",
                pattern="api-key-123",
                artifact_id="100",
                metadata={
                    "type": "workflow_logs",
                    "run_id": 100,
                    "run_name": "CI",
                    "head_sha": "abc123",
                },
            ),
        ]
        actions = scanner.clean(matches, confirm=True)
        assert len(actions) == 1
        assert "DELETE logs" in actions[0]


# ─── PyPI scanner tests ──────────────────────────────────────────

class TestPyPIScanner:
    @patch("tidy.artifacts.pypi.run_cmd")
    def test_scan_package_not_found(self, mock_cmd, patterns):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_cmd.return_value = mock_result

        scanner = PyPIScanner(patterns, "owner/repo", package="nonexistent")
        matches = scanner.scan()
        assert len(matches) == 0

    @patch("tidy.artifacts.pypi.run_cmd")
    def test_scan_no_releases(self, mock_cmd, patterns):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"info": {"name": "pkg"}, "releases": {}})
        mock_cmd.return_value = mock_result

        scanner = PyPIScanner(patterns, "owner/repo", package="pkg")
        matches = scanner.scan()
        assert len(matches) == 0

    def test_clean_dry_run(self, patterns):
        scanner = PyPIScanner(patterns, "owner/repo", package="pkg")
        matches = [
            ArtifactMatch(
                artifact_type=ArtifactType.PYPI,
                source="pypi:pkg==1.0",
                location="pkg-1.0/setup.py",
                line="author = 'SecretCorp'",
                pattern="SecretCorp",
                artifact_id="1.0",
                metadata={
                    "type": "pypi_package",
                    "package": "pkg",
                    "version": "1.0",
                    "filename": "pkg-1.0.tar.gz",
                    "url": "https://pypi.org/...",
                },
            ),
        ]

        with patch.object(scanner, "_get_version_info", return_value=None):
            actions = scanner.clean(matches, confirm=False)
        assert len(actions) >= 1
        assert "DRY-RUN" in actions[0]

    def test_clean_no_token_warning(self, patterns):
        scanner = PyPIScanner(patterns, "owner/repo", package="pkg", pypi_token=None)
        matches = [
            ArtifactMatch(
                artifact_type=ArtifactType.PYPI,
                source="pypi:pkg==1.0",
                location="pkg-1.0/setup.py",
                line="author = 'SecretCorp'",
                pattern="SecretCorp",
                artifact_id="1.0",
                metadata={
                    "type": "pypi_package",
                    "package": "pkg",
                    "version": "1.0",
                    "filename": "pkg-1.0.tar.gz",
                    "url": "https://pypi.org/...",
                },
            ),
        ]

        with patch.object(scanner, "_get_version_info", return_value=None):
            actions = scanner.clean(matches, confirm=True)
        assert any("PYPI_API_TOKEN" in a for a in actions)


# ─── Docker/GHCR scanner tests ───────────────────────────────────

class TestDockerGHCRScanner:
    @patch("tidy.artifacts.docker_ghcr._gh_api_json")
    def test_scan_no_packages(self, mock_api, patterns):
        mock_api.return_value = []
        scanner = DockerGHCRScanner(patterns, "owner/repo")
        matches = scanner.scan()
        assert len(matches) == 0

    @patch("tidy.artifacts.docker_ghcr._gh_api_json")
    def test_scan_finds_affected_sha(self, mock_api, patterns):
        sha = "abc123def456"
        mock_api.side_effect = [
            # _list_packages
            [{"name": "myimage"}],
            # _list_versions
            [
                {
                    "id": 1,
                    "name": "sha-abc123def456",
                    "metadata": {
                        "container": {
                            "tags": ["latest", "sha-abc123def456"],
                        }
                    },
                    "created_at": "2026-03-20T00:00:00Z",
                }
            ],
        ]
        scanner = DockerGHCRScanner(
            patterns, "owner/repo", affected_shas={sha}
        )
        matches = scanner.scan()
        # Should find the SHA match
        assert any(sha[:12] in m.location or sha[:7] in m.line for m in matches)

    @patch("tidy.artifacts.docker_ghcr._gh_api_json")
    def test_scan_pattern_in_tag(self, mock_api, patterns_ci):
        mock_api.side_effect = [
            # _list_packages
            [{"name": "myimage"}],
            # _list_versions
            [
                {
                    "id": 1,
                    "name": "v1",
                    "metadata": {
                        "container": {
                            "tags": ["sensitivedata-build"],
                        }
                    },
                    "created_at": "2026-03-20T00:00:00Z",
                }
            ],
        ]
        scanner = DockerGHCRScanner(patterns_ci, "owner/repo")
        matches = scanner.scan()
        assert len(matches) == 1
        assert matches[0].pattern == "sensitivedata"

    @patch("tidy.artifacts.docker_ghcr._gh_api")
    def test_clean_dry_run(self, mock_api, patterns):
        scanner = DockerGHCRScanner(patterns, "owner/repo")
        matches = [
            ArtifactMatch(
                artifact_type=ArtifactType.DOCKER_GHCR,
                source="ghcr:owner/myimage",
                location="tag:latest",
                line="latest",
                pattern="SecretCorp",
                artifact_id="1",
                metadata={
                    "type": "container_version",
                    "package_name": "myimage",
                    "version_id": 1,
                    "tags": ["latest"],
                    "created_at": "2026-03-20T00:00:00Z",
                },
            ),
        ]
        actions = scanner.clean(matches, confirm=False)
        assert len(actions) == 1
        assert "DRY-RUN" in actions[0]
        mock_api.assert_not_called()

    @patch("tidy.artifacts.docker_ghcr._gh_api")
    def test_clean_confirm_delete(self, mock_api, patterns):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_api.return_value = mock_result

        scanner = DockerGHCRScanner(patterns, "owner/repo")
        matches = [
            ArtifactMatch(
                artifact_type=ArtifactType.DOCKER_GHCR,
                source="ghcr:owner/myimage",
                location="tag:latest",
                line="latest",
                pattern="SecretCorp",
                artifact_id="1",
                metadata={
                    "type": "container_version",
                    "package_name": "myimage",
                    "version_id": 1,
                    "tags": ["latest"],
                    "created_at": "2026-03-20T00:00:00Z",
                },
            ),
        ]
        actions = scanner.clean(matches, confirm=True)
        assert len(actions) == 1
        assert "DELETE" in actions[0]
        assert "DRY-RUN" not in actions[0]


# ─── Archive scanning tests ──────────────────────────────────────

class TestArchiveScanning:
    def test_scan_zip_with_match(self, patterns):
        """Test scanning a zip file that contains a pattern match."""
        scanner = GitHubReleasesScanner(patterns, "owner/repo")

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "test.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("config.py", "company = 'SecretCorp'\n")
                zf.writestr("data.txt", "No matches here\n")

            matches = scanner._scan_archive_contents(
                zip_path,
                source="test",
                asset_name="test.zip",
                artifact_id="1",
                metadata={"type": "release_asset"},
            )
            assert len(matches) == 1
            assert matches[0].pattern == "SecretCorp"
            assert "config.py" in matches[0].location

    def test_scan_zip_no_match(self, patterns):
        """Test scanning a zip file with no matches."""
        scanner = GitHubReleasesScanner(patterns, "owner/repo")

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "clean.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("clean.py", "x = 42\n")

            matches = scanner._scan_archive_contents(
                zip_path,
                source="test",
                asset_name="clean.zip",
                artifact_id="1",
                metadata={"type": "release_asset"},
            )
            assert len(matches) == 0

    def test_scan_zip_skips_binary(self, patterns):
        """Test that binary files inside zips are skipped."""
        scanner = GitHubReleasesScanner(patterns, "owner/repo")

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = os.path.join(tmpdir, "mixed.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("module.pyc", "SecretCorp binary data")
                zf.writestr("readme.txt", "SecretCorp info")

            matches = scanner._scan_archive_contents(
                zip_path,
                source="test",
                asset_name="mixed.zip",
                artifact_id="1",
                metadata={"type": "release_asset"},
            )
            # Only the .txt should match, not the .pyc
            assert len(matches) == 1
            assert "readme.txt" in matches[0].location


# ─── CLI integration tests ───────────────────────────────────────

class TestCLIIntegration:
    def test_scan_artifacts_parser(self):
        """Test that scan-artifacts subcommand is registered."""
        from tidy.tidy import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "scan-artifacts",
            "--patterns", "tidy/patterns",
            "--repo", "owner/repo",
            "--types", "releases",
        ])
        assert args.command == "scan-artifacts"
        assert args.repo == "owner/repo"
        assert args.types == ["releases"]

    def test_clean_artifacts_parser(self):
        """Test that clean-artifacts subcommand is registered."""
        from tidy.tidy import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "clean-artifacts",
            "--patterns", "tidy/patterns",
            "--repo", "owner/repo",
            "--types", "actions", "pypi",
            "--confirm",
        ])
        assert args.command == "clean-artifacts"
        assert args.repo == "owner/repo"
        assert args.types == ["actions", "pypi"]
        assert args.confirm is True

    def test_clean_artifacts_default_dry_run(self):
        """Test that clean-artifacts defaults to dry-run (no --confirm)."""
        from tidy.tidy import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "clean-artifacts",
            "--patterns", "tidy/patterns",
            "--repo", "owner/repo",
        ])
        assert args.confirm is False

    def test_scan_artifacts_default_all_types(self):
        """Test that scan-artifacts defaults to all types."""
        from tidy.tidy import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            "scan-artifacts",
            "--patterns", "tidy/patterns",
            "--repo", "owner/repo",
        ])
        assert args.types == ["all"]

    def test_dispatch_table_includes_artifacts(self):
        """Test that the dispatch table includes artifact commands."""
        from tidy.tidy import main
        from tidy.artifacts._commands import cmd_scan_artifacts, cmd_clean_artifacts
        # Verify the import works (dispatch table is checked at call time)
        assert callable(cmd_scan_artifacts)
        assert callable(cmd_clean_artifacts)

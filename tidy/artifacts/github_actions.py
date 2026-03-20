"""GitHub Actions artifact scanner and cleaner.

Scans:
- Workflow run artifacts: downloads each artifact zip, extracts and scans
- Workflow run logs: downloads and scans log output for pattern matches

Cleans:
- Deletes artifacts containing matches
- Deletes workflow run logs for runs with matches
- Optionally deletes entire workflow runs

API reference:
- List artifacts: GET /repos/{owner}/{repo}/actions/artifacts
- Download artifact: GET /repos/{owner}/{repo}/actions/artifacts/{id}/zip
- Delete artifact: DELETE /repos/{owner}/{repo}/actions/artifacts/{id}
- List workflow runs: GET /repos/{owner}/{repo}/actions/runs
- Download run logs: GET /repos/{owner}/{repo}/actions/runs/{id}/logs
- Delete run logs: DELETE /repos/{owner}/{repo}/actions/runs/{id}/logs
- Delete run: DELETE /repos/{owner}/{repo}/actions/runs/{id}
"""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from typing import Any, Dict, List, Optional

from .base import ArtifactMatch, ArtifactScanner, ArtifactType
from .._config import Pattern
from .._github import _gh_api, _gh_api_json
from .._utils import log, run_cmd


class GitHubActionsScanner(ArtifactScanner):
    """Scan and clean GitHub Actions artifacts and workflow run logs."""

    artifact_type = ArtifactType.GITHUB_ACTIONS

    def __init__(
        self,
        patterns: List[Pattern],
        repo: str,
        *,
        max_runs: int = 100,
        max_artifacts: int = 200,
    ) -> None:
        """Initialize.

        Args:
            patterns: Patterns to search for.
            repo: GitHub repo in ``owner/repo`` format.
            max_runs: Maximum number of workflow runs to scan logs for.
            max_artifacts: Maximum number of artifacts to scan.
        """
        super().__init__(patterns, repo)
        self.max_runs = max_runs
        self.max_artifacts = max_artifacts

    # ─── API helpers ────────────────────────────────────────────────

    def _list_artifacts(self) -> List[Dict[str, Any]]:
        """List all workflow artifacts (paginated, up to max_artifacts)."""
        artifacts: List[Dict[str, Any]] = []
        page = 1
        while len(artifacts) < self.max_artifacts:
            data = _gh_api_json(
                f"/repos/{self.repo}/actions/artifacts?per_page=100&page={page}",
                check=False,
            )
            if not data or not isinstance(data, dict):
                break
            items = data.get("artifacts", [])
            if not items:
                break
            artifacts.extend(items)
            if len(items) < 100:
                break
            page += 1
        return artifacts[: self.max_artifacts]

    def _list_runs(self) -> List[Dict[str, Any]]:
        """List workflow runs (paginated, up to max_runs)."""
        runs: List[Dict[str, Any]] = []
        page = 1
        while len(runs) < self.max_runs:
            data = _gh_api_json(
                f"/repos/{self.repo}/actions/runs?per_page=100&page={page}",
                check=False,
            )
            if not data or not isinstance(data, dict):
                break
            items = data.get("workflow_runs", [])
            if not items:
                break
            runs.extend(items)
            if len(items) < 100:
                break
            page += 1
        return runs[: self.max_runs]

    def _download_artifact_zip(self, artifact_id: int, dest_path: str) -> bool:
        """Download an artifact zip to *dest_path*."""
        try:
            result = run_cmd(
                [
                    "gh", "api",
                    f"/repos/{self.repo}/actions/artifacts/{artifact_id}/zip",
                    "--output", dest_path,
                ],
                check=False,
            )
            return result.returncode == 0
        except Exception as exc:
            log(f"Error downloading artifact {artifact_id}: {exc}", level="WARN")
            return False

    def _download_run_logs(self, run_id: int, dest_path: str) -> bool:
        """Download workflow run logs as a zip to *dest_path*."""
        try:
            result = run_cmd(
                [
                    "gh", "api",
                    f"/repos/{self.repo}/actions/runs/{run_id}/logs",
                    "--output", dest_path,
                ],
                check=False,
            )
            return result.returncode == 0
        except Exception as exc:
            log(f"Error downloading logs for run {run_id}: {exc}", level="WARN")
            return False

    # ─── Scanning ───────────────────────────────────────────────────

    def _scan_zip_contents(
        self,
        zip_path: str,
        *,
        source: str,
        parent_name: str,
        artifact_id: str,
        metadata: Dict[str, Any],
    ) -> List[ArtifactMatch]:
        """Extract and scan a zip file for patterns."""
        matches: List[ArtifactMatch] = []
        try:
            if not zipfile.is_zipfile(zip_path):
                return matches

            with zipfile.ZipFile(zip_path, "r") as zf:
                for name in zf.namelist():
                    if _is_binary_extension(name):
                        continue
                    try:
                        content = zf.read(name).decode("utf-8", errors="replace")
                        matches.extend(
                            self._scan_text(
                                content,
                                source=source,
                                location=f"{parent_name}/{name}",
                                artifact_id=artifact_id,
                                metadata=metadata,
                            )
                        )
                    except Exception:
                        continue
        except Exception as exc:
            log(f"Error scanning zip {zip_path}: {exc}", level="WARN")
        return matches

    def scan(self) -> List[ArtifactMatch]:
        """Scan all artifacts and workflow run logs for pattern matches."""
        all_matches: List[ArtifactMatch] = []

        # 1. Scan workflow artifacts
        artifacts = self._list_artifacts()
        if artifacts:
            log(f"Scanning {len(artifacts)} workflow artifact(s) in {self.repo}...")
            for artifact in artifacts:
                art_name = artifact.get("name", "unknown")
                art_id = artifact.get("id")
                run_id = artifact.get("workflow_run", {}).get("id", "unknown")
                expired = artifact.get("expired", False)

                if expired:
                    continue

                source = f"artifact:{art_name} (run:{run_id})"

                with tempfile.TemporaryDirectory() as tmpdir:
                    dest = os.path.join(tmpdir, f"{art_name}.zip")
                    ok = self._download_artifact_zip(art_id, dest)
                    if not ok or not os.path.exists(dest):
                        log(f"  Skipping artifact {art_name} (download failed)", level="WARN")
                        continue

                    matches = self._scan_zip_contents(
                        dest,
                        source=source,
                        parent_name=art_name,
                        artifact_id=str(art_id),
                        metadata={
                            "type": "workflow_artifact",
                            "artifact_id": art_id,
                            "artifact_name": art_name,
                            "run_id": run_id,
                        },
                    )
                    all_matches.extend(matches)
        else:
            log("No workflow artifacts found", level="INFO")

        # 2. Scan workflow run logs
        runs = self._list_runs()
        if runs:
            log(f"Scanning logs for {len(runs)} workflow run(s) in {self.repo}...")
            for run in runs:
                run_id = run.get("id")
                run_name = run.get("name", "unknown")
                run_status = run.get("status", "unknown")
                head_sha = run.get("head_sha", "unknown")[:12]
                source = f"run:{run_id} ({run_name}, sha:{head_sha})"

                with tempfile.TemporaryDirectory() as tmpdir:
                    dest = os.path.join(tmpdir, f"run-{run_id}-logs.zip")
                    ok = self._download_run_logs(run_id, dest)
                    if not ok or not os.path.exists(dest):
                        # Logs may not be available for all runs
                        continue

                    matches = self._scan_zip_contents(
                        dest,
                        source=source,
                        parent_name=f"logs-run-{run_id}",
                        artifact_id=str(run_id),
                        metadata={
                            "type": "workflow_logs",
                            "run_id": run_id,
                            "run_name": run_name,
                            "head_sha": run.get("head_sha", ""),
                        },
                    )
                    all_matches.extend(matches)
        else:
            log("No workflow runs found", level="INFO")

        if all_matches:
            log(f"Found {len(all_matches)} match(es) in GitHub Actions", level="WARN")
        else:
            log("No matches found in GitHub Actions", level="OK")

        return all_matches

    # ─── Cleaning ───────────────────────────────────────────────────

    def clean(
        self,
        matches: List[ArtifactMatch],
        *,
        confirm: bool = False,
        replacement: str = "[REDACTED]",
    ) -> List[str]:
        """Clean Actions artifacts and logs containing matches.

        For workflow artifacts: deletes the entire artifact.
        For workflow logs: deletes the logs for the entire run.
        """
        actions: List[str] = []
        if not matches:
            return actions

        # Deduplicate
        artifact_ids: Dict[int, Dict[str, Any]] = {}
        run_ids_for_logs: Dict[int, Dict[str, Any]] = {}

        for m in matches:
            meta = m.metadata
            if meta.get("type") == "workflow_artifact":
                aid = meta["artifact_id"]
                artifact_ids[aid] = meta
            elif meta.get("type") == "workflow_logs":
                rid = meta["run_id"]
                run_ids_for_logs[rid] = meta

        # Delete artifacts
        for art_id, meta in artifact_ids.items():
            art_name = meta.get("artifact_name", "unknown")
            action = f"DELETE artifact '{art_name}' (artifact_id={art_id})"
            if confirm:
                ok = self._delete_artifact(art_id)
                if ok:
                    log(f"  {action}", level="OK")
                else:
                    action = f"FAILED: {action}"
                    log(f"  {action}", level="ERROR")
            else:
                action = f"DRY-RUN: would {action}"
                log(f"  {action}", level="INFO")
            actions.append(action)

        # Delete workflow run logs
        for run_id, meta in run_ids_for_logs.items():
            run_name = meta.get("run_name", "unknown")
            action = f"DELETE logs for run '{run_name}' (run_id={run_id})"
            if confirm:
                ok = self._delete_run_logs(run_id)
                if ok:
                    log(f"  {action}", level="OK")
                else:
                    action = f"FAILED: {action}"
                    log(f"  {action}", level="ERROR")
            else:
                action = f"DRY-RUN: would {action}"
                log(f"  {action}", level="INFO")
            actions.append(action)

        return actions

    def _delete_artifact(self, artifact_id: int) -> bool:
        """Delete a workflow artifact by ID."""
        result = _gh_api(
            f"/repos/{self.repo}/actions/artifacts/{artifact_id}",
            method="DELETE",
            check=False,
        )
        return result.returncode == 0

    def _delete_run_logs(self, run_id: int) -> bool:
        """Delete workflow run logs by run ID."""
        result = _gh_api(
            f"/repos/{self.repo}/actions/runs/{run_id}/logs",
            method="DELETE",
            check=False,
        )
        return result.returncode == 0


# ─── Helpers ────────────────────────────────────────────────────────────

_BINARY_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico",
    ".pdf", ".bin", ".dat", ".db", ".sqlite",
    ".o", ".a", ".lib", ".class",
})


def _is_binary_extension(filename: str) -> bool:
    """Return True if the filename has a known binary extension."""
    lower = filename.lower()
    for ext in _BINARY_EXTENSIONS:
        if lower.endswith(ext):
            return True
    return False

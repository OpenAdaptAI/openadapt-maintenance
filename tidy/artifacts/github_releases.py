"""GitHub Releases artifact scanner and cleaner.

Scans:
- Release body text (markdown) for pattern matches
- Release assets: downloads each asset to a temp file, scans for patterns
  (supports text files, zip archives, tar archives, and wheel files)

Cleans:
- Redacts release body text (PATCH the release)
- Deletes individual release assets that contain matches
- Optionally deletes entire releases

API reference:
- List releases: GET /repos/{owner}/{repo}/releases
- Get release: GET /repos/{owner}/{repo}/releases/{id}
- Update release: PATCH /repos/{owner}/{repo}/releases/{id}
- Delete release asset: DELETE /repos/{owner}/{repo}/releases/assets/{id}
- Delete release: DELETE /repos/{owner}/{repo}/releases/{id}
"""

from __future__ import annotations

import json
import os
import tarfile
import tempfile
import zipfile
from typing import Any, Dict, List, Optional

from .base import ArtifactMatch, ArtifactScanner, ArtifactType
from .._config import Pattern
from .._github import _gh_api, _gh_api_json
from .._utils import log, run_cmd


class GitHubReleasesScanner(ArtifactScanner):
    """Scan and clean GitHub Release body text and uploaded assets."""

    artifact_type = ArtifactType.GITHUB_RELEASES

    def __init__(self, patterns: List[Pattern], repo: str) -> None:
        """Initialize.

        Args:
            patterns: Patterns to search for.
            repo: GitHub repo in ``owner/repo`` format.
        """
        super().__init__(patterns, repo)

    # ─── API helpers ────────────────────────────────────────────────

    def _list_releases(self) -> List[Dict[str, Any]]:
        """List all releases for the repo (paginated)."""
        releases: List[Dict[str, Any]] = []
        page = 1
        while True:
            data = _gh_api_json(
                f"/repos/{self.repo}/releases?per_page=100&page={page}",
                check=False,
            )
            if not data or not isinstance(data, list):
                break
            releases.extend(data)
            if len(data) < 100:
                break
            page += 1
        return releases

    def _list_assets(self, release_id: int) -> List[Dict[str, Any]]:
        """List assets for a specific release."""
        data = _gh_api_json(
            f"/repos/{self.repo}/releases/{release_id}/assets",
            check=False,
        )
        if not data or not isinstance(data, list):
            return []
        return data

    def _download_asset(self, asset_url: str, dest_path: str) -> bool:
        """Download a release asset to *dest_path*.

        Uses ``gh api`` with the octet-stream accept header to download
        binary assets.
        """
        try:
            result = _gh_api(
                asset_url,
                accept="application/octet-stream",
                check=False,
            )
            if result.returncode != 0:
                log(f"Failed to download asset {asset_url}: {result.stderr}", level="WARN")
                return False
            with open(dest_path, "w", encoding="utf-8", errors="replace") as f:
                f.write(result.stdout)
            return True
        except Exception as exc:
            log(f"Error downloading asset {asset_url}: {exc}", level="WARN")
            return False

    def _download_asset_binary(self, asset_id: int, dest_path: str) -> bool:
        """Download a release asset as binary using gh CLI."""
        try:
            result = run_cmd(
                [
                    "gh", "release", "download",
                    "--repo", self.repo,
                    "--pattern", "*",
                    "--dir", os.path.dirname(dest_path),
                    "--clobber",
                ],
                check=False,
            )
            # Fallback: use curl with the gh token
            if result.returncode != 0:
                # Use the direct API download
                result = run_cmd(
                    [
                        "gh", "api",
                        f"/repos/{self.repo}/releases/assets/{asset_id}",
                        "-H", "Accept: application/octet-stream",
                        "--output", dest_path,
                    ],
                    check=False,
                )
            return result.returncode == 0
        except Exception as exc:
            log(f"Error downloading asset {asset_id}: {exc}", level="WARN")
            return False

    # ─── Scanning ───────────────────────────────────────────────────

    def _scan_text_content(
        self,
        content: str,
        *,
        source: str,
        location: str,
        artifact_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[ArtifactMatch]:
        """Scan text content for patterns."""
        return self._scan_text(
            content,
            source=source,
            location=location,
            artifact_id=artifact_id,
            metadata=metadata,
        )

    def _scan_archive_contents(
        self,
        archive_path: str,
        *,
        source: str,
        asset_name: str,
        artifact_id: str,
        metadata: Dict[str, Any],
    ) -> List[ArtifactMatch]:
        """Scan the contents of an archive (zip/tar/whl) for patterns."""
        matches: List[ArtifactMatch] = []

        # Try as zip/whl first
        if zipfile.is_zipfile(archive_path):
            try:
                with zipfile.ZipFile(archive_path, "r") as zf:
                    for name in zf.namelist():
                        # Skip binary files by extension
                        if _is_binary_extension(name):
                            continue
                        try:
                            content = zf.read(name).decode("utf-8", errors="replace")
                            matches.extend(
                                self._scan_text(
                                    content,
                                    source=source,
                                    location=f"{asset_name}/{name}",
                                    artifact_id=artifact_id,
                                    metadata=metadata,
                                )
                            )
                        except Exception:
                            continue
            except Exception as exc:
                log(f"Error scanning zip {archive_path}: {exc}", level="WARN")
            return matches

        # Try as tar
        try:
            if tarfile.is_tarfile(archive_path):
                with tarfile.open(archive_path, "r:*") as tf:
                    for member in tf.getmembers():
                        if not member.isfile() or _is_binary_extension(member.name):
                            continue
                        try:
                            f = tf.extractfile(member)
                            if f is None:
                                continue
                            content = f.read().decode("utf-8", errors="replace")
                            matches.extend(
                                self._scan_text(
                                    content,
                                    source=source,
                                    location=f"{asset_name}/{member.name}",
                                    artifact_id=artifact_id,
                                    metadata=metadata,
                                )
                            )
                        except Exception:
                            continue
        except Exception as exc:
            log(f"Error scanning tar {archive_path}: {exc}", level="WARN")

        return matches

    def scan(self) -> List[ArtifactMatch]:
        """Scan all releases for pattern matches."""
        all_matches: List[ArtifactMatch] = []
        releases = self._list_releases()

        if not releases:
            log("No releases found", level="INFO")
            return all_matches

        log(f"Scanning {len(releases)} release(s) in {self.repo}...")

        for release in releases:
            tag_name = release.get("tag_name", "unknown")
            release_id = release.get("id")
            release_name = release.get("name", tag_name)
            source = f"release:{tag_name} ({release_name})"

            # 1. Scan release body text
            body = release.get("body") or ""
            if body:
                body_matches = self._scan_text(
                    body,
                    source=source,
                    location="release body",
                    artifact_id=str(release_id),
                    metadata={
                        "type": "release_body",
                        "release_id": release_id,
                        "tag_name": tag_name,
                    },
                )
                all_matches.extend(body_matches)

            # 2. Scan release assets
            assets = self._list_assets(release_id)
            for asset in assets:
                asset_name = asset.get("name", "unknown")
                asset_id = asset.get("id")
                asset_url = asset.get("url", "")

                # Download to temp file and scan
                with tempfile.TemporaryDirectory() as tmpdir:
                    dest = os.path.join(tmpdir, asset_name)
                    ok = self._download_asset_binary(asset_id, dest)
                    if not ok or not os.path.exists(dest):
                        log(f"  Skipping asset {asset_name} (download failed)", level="WARN")
                        continue

                    asset_metadata = {
                        "type": "release_asset",
                        "release_id": release_id,
                        "asset_id": asset_id,
                        "asset_name": asset_name,
                        "tag_name": tag_name,
                    }

                    # Check if it is an archive
                    if _is_archive(asset_name):
                        matches = self._scan_archive_contents(
                            dest,
                            source=source,
                            asset_name=asset_name,
                            artifact_id=str(asset_id),
                            metadata=asset_metadata,
                        )
                        all_matches.extend(matches)
                    else:
                        # Scan as plain text
                        try:
                            with open(dest, "r", encoding="utf-8", errors="replace") as f:
                                content = f.read()
                            matches = self._scan_text(
                                content,
                                source=source,
                                location=f"asset:{asset_name}",
                                artifact_id=str(asset_id),
                                metadata=asset_metadata,
                            )
                            all_matches.extend(matches)
                        except Exception as exc:
                            log(f"  Error reading asset {asset_name}: {exc}", level="WARN")

        if all_matches:
            log(f"Found {len(all_matches)} match(es) in releases", level="WARN")
        else:
            log("No matches found in releases", level="OK")

        return all_matches

    # ─── Cleaning ───────────────────────────────────────────────────

    def clean(
        self,
        matches: List[ArtifactMatch],
        *,
        confirm: bool = False,
        replacement: str = "[REDACTED]",
    ) -> List[str]:
        """Clean release artifacts containing matches.

        For release body text: redacts the matching text.
        For release assets: deletes the entire asset.
        """
        actions: List[str] = []
        if not matches:
            return actions

        # Deduplicate by artifact type + ID
        body_releases: Dict[int, Dict[str, Any]] = {}
        asset_ids: Dict[int, Dict[str, Any]] = {}

        for m in matches:
            meta = m.metadata
            if meta.get("type") == "release_body":
                rid = meta["release_id"]
                body_releases[rid] = meta
            elif meta.get("type") == "release_asset":
                aid = meta["asset_id"]
                asset_ids[aid] = meta

        # Redact release body text
        for release_id, meta in body_releases.items():
            tag = meta.get("tag_name", "unknown")
            action = f"REDACT release body for {tag} (release_id={release_id})"
            if confirm:
                ok = self._redact_release_body(release_id, replacement)
                if ok:
                    log(f"  {action}", level="OK")
                else:
                    action = f"FAILED: {action}"
                    log(f"  {action}", level="ERROR")
            else:
                action = f"DRY-RUN: would {action}"
                log(f"  {action}", level="INFO")
            actions.append(action)

        # Delete contaminated assets
        for asset_id, meta in asset_ids.items():
            asset_name = meta.get("asset_name", "unknown")
            tag = meta.get("tag_name", "unknown")
            action = f"DELETE asset '{asset_name}' from release {tag} (asset_id={asset_id})"
            if confirm:
                ok = self._delete_asset(asset_id)
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

    def _redact_release_body(self, release_id: int, replacement: str) -> bool:
        """Redact patterns from a release body via PATCH."""
        data = _gh_api_json(
            f"/repos/{self.repo}/releases/{release_id}",
            check=False,
        )
        if not data:
            return False

        body = data.get("body") or ""
        new_body = body
        for pattern in self.patterns:
            if pattern.case_sensitive:
                new_body = new_body.replace(pattern.text, replacement)
            else:
                # Case-insensitive replacement
                import re
                new_body = re.sub(
                    re.escape(pattern.text), replacement, new_body, flags=re.IGNORECASE
                )

        if new_body == body:
            return True  # Nothing to change

        payload = json.dumps({"body": new_body})
        result = _gh_api(
            f"/repos/{self.repo}/releases/{release_id}",
            method="PATCH",
            input_data=payload,
            check=False,
        )
        return result.returncode == 0

    def _delete_asset(self, asset_id: int) -> bool:
        """Delete a release asset by ID."""
        result = _gh_api(
            f"/repos/{self.repo}/releases/assets/{asset_id}",
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

_ARCHIVE_EXTENSIONS = frozenset({
    ".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz",
    ".whl", ".egg",
})


def _is_binary_extension(filename: str) -> bool:
    """Return True if the filename has a known binary extension."""
    lower = filename.lower()
    for ext in _BINARY_EXTENSIONS:
        if lower.endswith(ext):
            return True
    return False


def _is_archive(filename: str) -> bool:
    """Return True if the filename looks like an archive."""
    lower = filename.lower()
    for ext in _ARCHIVE_EXTENSIONS:
        if lower.endswith(ext):
            return True
    return False

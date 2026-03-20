"""PyPI package scanner and cleaner.

Scans:
- Lists all published versions of a PyPI package
- Downloads wheels and sdists for each version
- Extracts and scans file contents for pattern matches

Cleans:
- Yanks affected versions via PyPI API (hides from default pip install)
- Flags versions that need manual PyPI admin contact for deletion

Constraints:
- Yanking does NOT remove the package -- users with ``pip install pkg==X.Y.Z``
  can still download it
- True deletion within 72 hours: web UI only (not automatable)
- True deletion after 72 hours: requires emailing security@pypi.org
- Cannot re-upload the same version after deletion -- must bump version

API reference:
- Simple API: GET https://pypi.org/simple/{package}/
- JSON API: GET https://pypi.org/pypi/{package}/json
- Version JSON: GET https://pypi.org/pypi/{package}/{version}/json
- Yank: Requires PyPI API token with project scope

Note: This scanner requires ``pip`` or ``curl`` for downloading packages.
PyPI yanking requires a ``PYPI_API_TOKEN`` environment variable.
"""

from __future__ import annotations

import json
import os
import tarfile
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .base import ArtifactMatch, ArtifactScanner, ArtifactType
from .._config import Pattern
from .._utils import log, run_cmd


class PyPIScanner(ArtifactScanner):
    """Scan and clean PyPI published packages."""

    artifact_type = ArtifactType.PYPI

    def __init__(
        self,
        patterns: List[Pattern],
        repo: str,
        *,
        package: Optional[str] = None,
        pypi_token: Optional[str] = None,
    ) -> None:
        """Initialize.

        Args:
            patterns: Patterns to search for.
            repo: GitHub repo in ``owner/repo`` format (used for context).
            package: PyPI package name.  If None, inferred from repo name.
            pypi_token: PyPI API token for yanking.  If None, reads from
                ``PYPI_API_TOKEN`` environment variable.
        """
        super().__init__(patterns, repo)
        self.package = package or repo.split("/")[-1] if "/" in repo else repo
        self.pypi_token = pypi_token or os.environ.get("PYPI_API_TOKEN")

    # ─── API helpers ────────────────────────────────────────────────

    def _get_package_info(self) -> Optional[Dict[str, Any]]:
        """Fetch package metadata from PyPI JSON API."""
        try:
            result = run_cmd(
                ["curl", "-sS", f"https://pypi.org/pypi/{self.package}/json"],
                check=False,
            )
            if result.returncode != 0:
                return None
            return json.loads(result.stdout)
        except (json.JSONDecodeError, Exception) as exc:
            log(f"Error fetching PyPI info for {self.package}: {exc}", level="WARN")
            return None

    def _get_version_info(self, version: str) -> Optional[Dict[str, Any]]:
        """Fetch version-specific metadata from PyPI."""
        try:
            result = run_cmd(
                ["curl", "-sS", f"https://pypi.org/pypi/{self.package}/{version}/json"],
                check=False,
            )
            if result.returncode != 0:
                return None
            return json.loads(result.stdout)
        except (json.JSONDecodeError, Exception) as exc:
            log(f"Error fetching PyPI version info for {self.package}=={version}: {exc}", level="WARN")
            return None

    def _download_file(self, url: str, dest_path: str) -> bool:
        """Download a file from a URL."""
        try:
            result = run_cmd(
                ["curl", "-sSL", "-o", dest_path, url],
                check=False,
            )
            return result.returncode == 0 and os.path.exists(dest_path)
        except Exception as exc:
            log(f"Error downloading {url}: {exc}", level="WARN")
            return False

    # ─── Scanning ───────────────────────────────────────────────────

    def _scan_archive(
        self,
        archive_path: str,
        *,
        source: str,
        filename: str,
        version: str,
        file_url: str,
    ) -> List[ArtifactMatch]:
        """Scan an archive (wheel/sdist) for patterns."""
        matches: List[ArtifactMatch] = []
        metadata = {
            "type": "pypi_package",
            "package": self.package,
            "version": version,
            "filename": filename,
            "url": file_url,
        }

        # Try as zip/whl first
        if zipfile.is_zipfile(archive_path):
            try:
                with zipfile.ZipFile(archive_path, "r") as zf:
                    for name in zf.namelist():
                        if _is_binary_extension(name):
                            continue
                        try:
                            content = zf.read(name).decode("utf-8", errors="replace")
                            matches.extend(
                                self._scan_text(
                                    content,
                                    source=source,
                                    location=f"{filename}/{name}",
                                    artifact_id=version,
                                    metadata=metadata,
                                )
                            )
                        except Exception:
                            continue
            except Exception as exc:
                log(f"Error scanning wheel/zip {filename}: {exc}", level="WARN")
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
                                    location=f"{filename}/{member.name}",
                                    artifact_id=version,
                                    metadata=metadata,
                                )
                            )
                        except Exception:
                            continue
        except Exception as exc:
            log(f"Error scanning sdist {filename}: {exc}", level="WARN")

        return matches

    def scan(self) -> List[ArtifactMatch]:
        """Scan all published PyPI versions for pattern matches."""
        all_matches: List[ArtifactMatch] = []

        info = self._get_package_info()
        if not info:
            log(f"Package '{self.package}' not found on PyPI", level="WARN")
            return all_matches

        releases = info.get("releases", {})
        if not releases:
            log(f"No releases found for '{self.package}' on PyPI", level="INFO")
            return all_matches

        log(f"Scanning {len(releases)} version(s) of '{self.package}' on PyPI...")

        for version, files in releases.items():
            if not files:
                continue

            source = f"pypi:{self.package}=={version}"

            for file_info in files:
                filename = file_info.get("filename", "unknown")
                url = file_info.get("url", "")
                packagetype = file_info.get("packagetype", "")
                yanked = file_info.get("yanked", False)

                if yanked:
                    continue  # Already yanked, skip

                # Only scan wheels and sdists (not eggs or other legacy formats)
                if packagetype not in ("sdist", "bdist_wheel"):
                    continue

                with tempfile.TemporaryDirectory() as tmpdir:
                    dest = os.path.join(tmpdir, filename)
                    ok = self._download_file(url, dest)
                    if not ok:
                        log(f"  Skipping {filename} (download failed)", level="WARN")
                        continue

                    matches = self._scan_archive(
                        dest,
                        source=source,
                        filename=filename,
                        version=version,
                        file_url=url,
                    )
                    all_matches.extend(matches)

        if all_matches:
            log(f"Found {len(all_matches)} match(es) in PyPI packages", level="WARN")
        else:
            log("No matches found in PyPI packages", level="OK")

        return all_matches

    # ─── Cleaning ───────────────────────────────────────────────────

    def clean(
        self,
        matches: List[ArtifactMatch],
        *,
        confirm: bool = False,
        replacement: str = "[REDACTED]",
    ) -> List[str]:
        """Yank affected PyPI versions and report manual actions needed.

        PyPI does not support automated deletion or content replacement.
        This method:
        1. Yanks affected versions (if API token is available)
        2. Reports which versions need manual attention
        3. Provides instructions for contacting PyPI admins
        """
        actions: List[str] = []
        if not matches:
            return actions

        # Collect affected versions
        affected_versions: Dict[str, Dict[str, Any]] = {}
        for m in matches:
            version = m.metadata.get("version", "unknown")
            if version not in affected_versions:
                affected_versions[version] = {
                    "version": version,
                    "match_count": 0,
                    "filenames": set(),
                }
            affected_versions[version]["match_count"] += 1
            affected_versions[version]["filenames"].add(
                m.metadata.get("filename", "unknown")
            )

        for version, info in affected_versions.items():
            count = info["match_count"]
            files = ", ".join(sorted(info["filenames"]))

            # Check if version is within 72-hour deletion window
            version_info = self._get_version_info(version)
            within_window = False
            if version_info:
                upload_time = _parse_upload_time(version_info)
                if upload_time:
                    age = datetime.now(timezone.utc) - upload_time
                    within_window = age < timedelta(hours=72)

            if within_window:
                action = (
                    f"MANUAL ACTION REQUIRED: {self.package}=={version} "
                    f"({count} match(es) in {files}) — within 72h window, "
                    f"can be deleted via PyPI web UI at "
                    f"https://pypi.org/manage/project/{self.package}/release/{version}/"
                )
                log(f"  {action}", level="WARN")
                actions.append(action)
            else:
                # Try to yank
                yank_action = f"YANK {self.package}=={version} ({count} match(es) in {files})"
                if confirm:
                    if self.pypi_token:
                        ok = self._yank_version(version)
                        if ok:
                            log(f"  {yank_action}", level="OK")
                        else:
                            yank_action = f"FAILED: {yank_action}"
                            log(f"  {yank_action}", level="ERROR")
                    else:
                        yank_action = (
                            f"SKIPPED (no PYPI_API_TOKEN): {yank_action} — "
                            f"set PYPI_API_TOKEN env var to enable automated yanking"
                        )
                        log(f"  {yank_action}", level="WARN")
                else:
                    yank_action = f"DRY-RUN: would {yank_action}"
                    log(f"  {yank_action}", level="INFO")
                actions.append(yank_action)

                # Also note that yanking does not fully remove the package
                note = (
                    f"NOTE: Yanking {self.package}=={version} hides it from "
                    f"default pip install, but it remains downloadable with "
                    f"explicit version pinning. For full deletion (>72h old), "
                    f"contact security@pypi.org."
                )
                log(f"  {note}", level="INFO")
                actions.append(note)

        return actions

    def _yank_version(self, version: str) -> bool:
        """Yank a PyPI version.

        Uses the PyPI JSON API to yank a release.  Requires a PyPI API token
        with project-level scope.

        Note: The yank endpoint is not well-documented.  This uses the
        warehouse API endpoint used by twine/flit.
        """
        if not self.pypi_token:
            log("No PyPI API token available", level="ERROR")
            return False

        try:
            result = run_cmd(
                [
                    "curl", "-sSf",
                    "-X", "POST",
                    "-H", f"Authorization: Bearer {self.pypi_token}",
                    "-d", f"id={self.package}&version={version}",
                    f"https://pypi.org/manage/project/{self.package}/release/{version}/yank/",
                ],
                check=False,
            )
            if result.returncode != 0:
                log(f"Yank request failed for {self.package}=={version}: {result.stderr}", level="ERROR")
                return False
            return True
        except Exception as exc:
            log(f"Error yanking {self.package}=={version}: {exc}", level="ERROR")
            return False


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


def _parse_upload_time(version_info: Dict[str, Any]) -> Optional[datetime]:
    """Parse the upload time from PyPI version info."""
    try:
        urls = version_info.get("urls", [])
        if urls:
            upload_time_str = urls[0].get("upload_time_iso_8601")
            if upload_time_str:
                return datetime.fromisoformat(upload_time_str.replace("Z", "+00:00"))
    except Exception:
        pass
    return None

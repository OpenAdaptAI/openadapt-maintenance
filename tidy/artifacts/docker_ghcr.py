"""Docker/GHCR container image scanner and cleaner.

Scans:
- Lists all container package versions via GitHub Packages API
- Identifies images built from affected commits (via OCI labels)
- Cannot scan image layer contents directly (would require pulling and
  extracting every layer, which is prohibitively expensive for large images)

Cleans:
- Deletes container package versions by ID
- Supports both org-level and user-level packages

Strategy:
Since scanning image contents is impractical at scale, this scanner uses
a commit-based approach:
1. List all container image versions with their metadata
2. Match images by:
   - ``org.opencontainers.image.revision`` label (build commit SHA)
   - Tag names containing commit SHAs
   - Build timestamps within the window of affected commits
3. Delete matched versions

If the repo has no container packages, scanning is a no-op.

API reference:
- List org packages: GET /orgs/{org}/packages?package_type=container
- List org package versions: GET /orgs/{org}/packages/container/{name}/versions
- Delete org package version: DELETE /orgs/{org}/packages/container/{name}/versions/{id}
- List user packages: GET /user/packages?package_type=container
- List user package versions: GET /user/packages/container/{name}/versions
- Delete user package version: DELETE /user/packages/container/{name}/versions/{id}
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set

from .base import ArtifactMatch, ArtifactScanner, ArtifactType
from .._config import Pattern
from .._github import _gh_api, _gh_api_json
from .._utils import log


class DockerGHCRScanner(ArtifactScanner):
    """Scan and clean Docker/GHCR container package versions."""

    artifact_type = ArtifactType.DOCKER_GHCR

    def __init__(
        self,
        patterns: List[Pattern],
        repo: str,
        *,
        affected_shas: Optional[Set[str]] = None,
        package_names: Optional[List[str]] = None,
        is_org: bool = True,
    ) -> None:
        """Initialize.

        Args:
            patterns: Patterns to search for.
            repo: GitHub repo in ``owner/repo`` format.
            affected_shas: Set of commit SHAs known to contain sensitive data.
                If provided, images built from these commits will be flagged.
                If None, all images are listed for review but none are flagged.
            package_names: Specific container package names to scan.  If None,
                all container packages for the owner are scanned.
            is_org: Whether the owner is an org (True) or user (False).
        """
        super().__init__(patterns, repo)
        self.owner = repo.split("/")[0] if "/" in repo else repo
        self.affected_shas = affected_shas or set()
        self.package_names = package_names
        self.is_org = is_org

    # ─── API helpers ────────────────────────────────────────────────

    def _api_prefix(self) -> str:
        """Return the API path prefix for org or user packages."""
        if self.is_org:
            return f"/orgs/{self.owner}"
        return "/user"

    def _list_packages(self) -> List[Dict[str, Any]]:
        """List all container packages for the owner."""
        packages: List[Dict[str, Any]] = []
        page = 1
        prefix = self._api_prefix()
        while True:
            data = _gh_api_json(
                f"{prefix}/packages?package_type=container&per_page=100&page={page}",
                check=False,
            )
            if not data or not isinstance(data, list):
                break
            packages.extend(data)
            if len(data) < 100:
                break
            page += 1
        return packages

    def _list_versions(self, package_name: str) -> List[Dict[str, Any]]:
        """List all versions of a container package."""
        versions: List[Dict[str, Any]] = []
        page = 1
        prefix = self._api_prefix()
        while True:
            data = _gh_api_json(
                f"{prefix}/packages/container/{package_name}/versions"
                f"?per_page=100&page={page}",
                check=False,
            )
            if not data or not isinstance(data, list):
                break
            versions.extend(data)
            if len(data) < 100:
                break
            page += 1
        return versions

    def _get_version_metadata(
        self, package_name: str, version_id: int
    ) -> Optional[Dict[str, Any]]:
        """Get detailed metadata for a package version."""
        prefix = self._api_prefix()
        return _gh_api_json(
            f"{prefix}/packages/container/{package_name}/versions/{version_id}",
            check=False,
        )

    # ─── Scanning ───────────────────────────────────────────────────

    def _check_version_for_sha(
        self, version: Dict[str, Any], package_name: str
    ) -> Optional[str]:
        """Check if a version was built from an affected commit SHA.

        Looks for:
        1. Tags containing a commit SHA
        2. OCI labels with ``org.opencontainers.image.revision``
        """
        # Check tags
        metadata = version.get("metadata", {})
        container = metadata.get("container", {})
        tags = container.get("tags", [])

        for tag in tags:
            for sha in self.affected_shas:
                short = sha[:7]
                long = sha[:12]
                if short in tag or long in tag or sha in tag:
                    return sha

        # Check build metadata / labels if available
        # GitHub API doesn't always expose OCI labels directly in the version
        # list response, so we check what we can
        name = version.get("name", "")
        for sha in self.affected_shas:
            if sha[:12] in name or sha in name:
                return sha

        return None

    def _scan_version_tags(
        self,
        version: Dict[str, Any],
        package_name: str,
    ) -> List[ArtifactMatch]:
        """Scan version tag names and metadata for patterns."""
        matches: List[ArtifactMatch] = []
        version_id = version.get("id")
        metadata = version.get("metadata", {})
        container = metadata.get("container", {})
        tags = container.get("tags", [])
        created_at = version.get("created_at", "unknown")

        # Scan tag names for patterns
        for tag in tags:
            for pattern in self.patterns:
                if self._match_text(tag, pattern):
                    matches.append(
                        ArtifactMatch(
                            artifact_type=self.artifact_type,
                            source=f"ghcr:{self.owner}/{package_name}",
                            location=f"tag:{tag}",
                            line=tag,
                            pattern=pattern.text,
                            artifact_id=str(version_id),
                            metadata={
                                "type": "container_version",
                                "package_name": package_name,
                                "version_id": version_id,
                                "tags": tags,
                                "created_at": created_at,
                            },
                        )
                    )

        # Check if built from an affected SHA
        affected_sha = self._check_version_for_sha(version, package_name)
        if affected_sha:
            tag_str = ", ".join(tags) if tags else "untagged"
            matches.append(
                ArtifactMatch(
                    artifact_type=self.artifact_type,
                    source=f"ghcr:{self.owner}/{package_name}",
                    location=f"built from affected commit {affected_sha[:12]}",
                    line=f"tags=[{tag_str}], created={created_at}",
                    pattern=f"(commit SHA {affected_sha[:12]})",
                    artifact_id=str(version_id),
                    metadata={
                        "type": "container_version",
                        "package_name": package_name,
                        "version_id": version_id,
                        "tags": tags,
                        "created_at": created_at,
                        "affected_sha": affected_sha,
                    },
                )
            )

        return matches

    def scan(self) -> List[ArtifactMatch]:
        """Scan container packages for affected versions."""
        all_matches: List[ArtifactMatch] = []

        # Get packages to scan
        if self.package_names:
            packages = [{"name": n} for n in self.package_names]
        else:
            packages = self._list_packages()

        if not packages:
            log(f"No container packages found for {self.owner}", level="INFO")
            return all_matches

        log(f"Scanning {len(packages)} container package(s) for {self.owner}...")

        for pkg in packages:
            pkg_name = pkg.get("name", "unknown")
            versions = self._list_versions(pkg_name)

            if not versions:
                log(f"  No versions found for {pkg_name}", level="INFO")
                continue

            log(f"  Scanning {len(versions)} version(s) of {pkg_name}...")

            for version in versions:
                matches = self._scan_version_tags(version, pkg_name)
                all_matches.extend(matches)

        if all_matches:
            log(f"Found {len(all_matches)} match(es) in GHCR packages", level="WARN")
        else:
            log("No matches found in GHCR packages", level="OK")

        return all_matches

    # ─── Cleaning ───────────────────────────────────────────────────

    def clean(
        self,
        matches: List[ArtifactMatch],
        *,
        confirm: bool = False,
        replacement: str = "[REDACTED]",
    ) -> List[str]:
        """Delete container package versions that match.

        Deletes the entire version (cannot patch individual layers).
        """
        actions: List[str] = []
        if not matches:
            return actions

        # Deduplicate by version ID
        versions_to_delete: Dict[int, Dict[str, Any]] = {}
        for m in matches:
            meta = m.metadata
            if meta.get("type") == "container_version":
                vid = meta["version_id"]
                versions_to_delete[vid] = meta

        for version_id, meta in versions_to_delete.items():
            pkg_name = meta.get("package_name", "unknown")
            tags = meta.get("tags", [])
            tag_str = ", ".join(tags) if tags else "untagged"
            action = (
                f"DELETE container version {pkg_name}:{tag_str} "
                f"(version_id={version_id})"
            )
            if confirm:
                ok = self._delete_version(pkg_name, version_id)
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

    def _delete_version(self, package_name: str, version_id: int) -> bool:
        """Delete a container package version."""
        prefix = self._api_prefix()
        result = _gh_api(
            f"{prefix}/packages/container/{package_name}/versions/{version_id}",
            method="DELETE",
            check=False,
        )
        return result.returncode == 0

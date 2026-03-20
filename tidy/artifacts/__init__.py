"""Build artifact scanning and cleaning for the tidy tool.

Extends tidy's git-history scrubbing with coverage for published artifacts:
- GitHub Releases (assets and body text)
- GitHub Actions (workflow artifacts and run logs)
- PyPI packages (wheels and sdists)
- Docker/GHCR images (container package versions)
"""

from .base import ArtifactMatch, ArtifactScanner, ArtifactType
from .github_releases import GitHubReleasesScanner
from .github_actions import GitHubActionsScanner
from .pypi import PyPIScanner
from .docker_ghcr import DockerGHCRScanner

__all__ = [
    "ArtifactMatch",
    "ArtifactScanner",
    "ArtifactType",
    "GitHubReleasesScanner",
    "GitHubActionsScanner",
    "PyPIScanner",
    "DockerGHCRScanner",
]

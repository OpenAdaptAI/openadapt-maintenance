"""Aggregate CHANGELOGs from all repos into a unified changelog page."""

import pathlib
import requests
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPOS_YML = ROOT / "repos.yml"
DOCS_DIR = ROOT / "docs"

GITHUB_API = "https://api.github.com"


def load_repos(path=REPOS_YML):
    with open(path) as f:
        return [r for r in yaml.safe_load(f)["repos"] if r.get("changelog")]


def fetch_releases(github_slug, per_page=5):
    """Fetch recent releases from GitHub API."""
    url = f"{GITHUB_API}/repos/{github_slug}/releases?per_page={per_page}"
    resp = requests.get(url, timeout=15)
    if resp.status_code != 200:
        return []
    return [
        {"tag": r["tag_name"], "date": r["published_at"][:10],
         "url": r["html_url"], "body": (r.get("body") or "").strip()[:200]}
        for r in resp.json() if not r.get("draft")
    ]


def aggregate(repos=None, docs_dir=None):
    repos = repos or load_repos()
    docs_dir = pathlib.Path(docs_dir or DOCS_DIR)

    lines = ["# Changelog\n", "> Aggregated from all OpenAdapt repositories.\n"]
    for repo in repos:
        releases = fetch_releases(repo["github"])
        if not releases:
            continue
        lines.append(f"\n## {repo['name']}\n")
        for r in releases:
            lines.append(f"- **[{r['tag']}]({r['url']})** ({r['date']})")
            if r["body"]:
                summary = r["body"].split("\n")[0]
                lines.append(f"  {summary}")

    out_path = docs_dir / "changelog.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n")
    print(f"Wrote {out_path} ({len(lines)} lines)")
    return str(out_path)


if __name__ == "__main__":
    aggregate()

"""Generate an architecture overview page, optionally using LLM."""

import os
import pathlib
import yaml
import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPOS_YML = ROOT / "repos.yml"
DOCS_DIR = ROOT / "docs"
PROMPTS_DIR = ROOT / "prompts"

GITHUB_API = "https://api.github.com"


def load_repos(path=REPOS_YML):
    with open(path) as f:
        return yaml.safe_load(f)["repos"]


def fetch_readme(github_slug):
    url = f"{GITHUB_API}/repos/{github_slug}/readme"
    headers = {"Accept": "application/vnd.github.raw+json"}
    resp = requests.get(url, headers=headers, timeout=15)
    return resp.text if resp.status_code == 200 else ""


def build_static_overview(repos):
    """Build a minimal architecture page without LLM."""
    lines = [
        "# Architecture Overview\n",
        "> *This is an auto-generated overview of the OpenAdapt ecosystem.*\n",
    ]
    by_cat = {}
    for r in repos:
        by_cat.setdefault(r.get("category", "core"), []).append(r)

    for cat, label in [("core", "Core Packages"), ("devtools", "Developer Tools")]:
        if cat not in by_cat:
            continue
        lines.append(f"\n## {label}\n")
        for r in by_cat[cat]:
            lines.append(
                f"- **[{r['name']}](https://github.com/{r['github']})** — "
                f"See [{r['name']} docs]({r['doc_page']})"
            )
    return "\n".join(lines) + "\n"


def build(repos=None, docs_dir=None):
    repos = repos or load_repos()
    docs_dir = pathlib.Path(docs_dir or DOCS_DIR)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import anthropic
            readmes = {r["name"]: fetch_readme(r["github"]) for r in repos}
            readmes_text = "\n\n---\n\n".join(
                f"## {name}\n{text}" for name, text in readmes.items() if text
            )
            prompt = (PROMPTS_DIR / "architecture.txt").read_text()
            prompt = prompt.format(readmes_text=readmes_text)

            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}],
            )
            content = response.content[0].text
        except Exception as e:
            print(f"  LLM failed ({e}), falling back to static overview")
            content = build_static_overview(repos)
    else:
        print("  No ANTHROPIC_API_KEY, building static overview")
        content = build_static_overview(repos)

    out_path = docs_dir / "architecture.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content)
    print(f"Wrote {out_path}")
    return str(out_path)


if __name__ == "__main__":
    build()

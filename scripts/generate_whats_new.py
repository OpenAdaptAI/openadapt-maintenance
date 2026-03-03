"""Generate a 'What's New' digest from recent merged PRs, optionally using LLM."""

import datetime
import json
import os
import pathlib
import subprocess
import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPOS_YML = ROOT / "repos.yml"
DOCS_DIR = ROOT / "docs"
TEMPLATES_DIR = ROOT / "templates"
PROMPTS_DIR = ROOT / "prompts"


def load_repos(path=REPOS_YML):
    with open(path) as f:
        return yaml.safe_load(f)["repos"]


def fetch_merged_prs(github_slug, days=7):
    """Fetch merged PRs from the last N days using gh CLI."""
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    cmd = [
        "gh", "pr", "list", "--repo", github_slug, "--state", "merged",
        "--json", "title,url,number,mergedAt", "--limit", "20",
        "--search", f"merged:>={since[:10]}",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return []


def llm_summarize(prs_text, days):
    """Call Anthropic API to summarize PRs. Returns None if no API key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  No ANTHROPIC_API_KEY set, skipping LLM summary")
        return None

    try:
        import anthropic
    except ImportError:
        print("  anthropic package not installed, skipping LLM summary")
        return None

    prompt_template = (PROMPTS_DIR / "whats_new.txt").read_text()
    prompt = prompt_template.format(days=days, prs_text=prs_text)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def generate(repos=None, docs_dir=None, days=7):
    repos = repos or load_repos()
    docs_dir = pathlib.Path(docs_dir or DOCS_DIR)

    all_prs = {}
    prs_text_parts = []
    for repo in repos:
        slug = repo["github"]
        prs = fetch_merged_prs(slug, days=days)
        if prs:
            all_prs[repo["name"]] = prs
            for pr in prs:
                prs_text_parts.append(
                    f"[{repo['name']}] {pr['title']} (#{pr['number']}) {pr['url']}"
                )
        print(f"  {repo['name']}: {len(prs)} merged PRs")

    prs_text = "\n".join(prs_text_parts) if prs_text_parts else "No PRs found."
    summary = llm_summarize(prs_text, days)

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template = env.get_template("whats_new.md.j2")
    generated_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    rendered = template.render(
        repos=all_prs, summary=summary, days=days, generated_at=generated_at,
    )
    out_path = docs_dir / "whats-new.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered)
    print(f"Wrote {out_path}")
    return str(out_path)


if __name__ == "__main__":
    generate()

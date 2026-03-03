# openadapt-maintenance

Automated documentation generator for the OpenAdapt ecosystem. Syncs READMEs, aggregates changelogs, and generates digest pages using LLM.

## Quick Start

```bash
# Install dependencies
uv sync

# Sync all READMEs into docs/
python scripts/sync_readmes.py

# Aggregate changelogs
python scripts/aggregate_changelog.py

# Generate What's New (optional: set ANTHROPIC_API_KEY for LLM summary)
python scripts/generate_whats_new.py

# Build and preview the docs site
uv run mkdocs serve
```

## Adding a New Repo

Add an entry to `repos.yml` — no code changes needed.

## Architecture

- **Mechanical sync** (sync_readmes, aggregate_changelog): deterministic, no API calls
- **LLM-enhanced** (generate_whats_new, build_architecture): optional, gracefully degrades without API key
- **MkDocs Material**: builds to static site, deployable on GitHub Pages

## Tests

```bash
uv sync --extra dev
uv run pytest tests/ -v
```

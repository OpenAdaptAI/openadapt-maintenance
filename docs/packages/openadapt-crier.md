# openadapt-crier

[![GitHub](https://img.shields.io/github/stars/OpenAdaptAI/openadapt-crier?style=social)](https://github.com/OpenAdaptAI/openadapt-crier)

> *Auto-generated from [OpenAdaptAI/openadapt-crier](https://github.com/OpenAdaptAI/openadapt-crier). Last synced: 2026-03-21 18:47 UTC*

---

# openadapt-crier

Event-driven social media approval bot with Telegram.

## How it works

Crier watches GitHub repositories for new commits, releases, and merged PRs. When activity is detected, it uses an LLM (Claude) to score how interesting the event is. Events that pass the interest threshold get platform-specific draft posts generated automatically. Drafts are sent to a Telegram bot for human review, where you can approve, edit, or reject them. On approval, posts are dispatched to Discord, Twitter/X, and LinkedIn via [openadapt-herald](https://github.com/OpenAdaptAI/openadapt-herald).

```
GitHub Events ──> Watcher ──> Drafter (LLM) ──> Telegram Bot ──> Dispatcher ──> Herald ──> Social Platforms
  (commits,        (polls       (scores           (approve,       (posts to      (publish)   (Discord,
   releases,        GitHub       interest,          edit, or        selected                   Twitter,
   merged PRs)      API)         generates          reject)         platforms)                 LinkedIn)
                                 drafts)
```

## Quick start

```bash
pip install .

# Required env vars
export CRIER_TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
export CRIER_TELEGRAM_OWNER_ID="your-telegram-user-id"
export CRIER_REPOS="owner/repo1,owner/repo2"
export ANTHROPIC_API_KEY="your-anthropic-key"

# Optional: platform credentials for posting
export DISCORD_WEBHOOK_URL="..."
export TWITTER_CONSUMER_KEY="..."
export TWITTER_CONSUMER_SECRET="..."
export TWITTER_ACCESS_TOKEN="..."
export TWITTER_ACCESS_TOKEN_SECRET="..."
export LINKEDIN_ACCESS_TOKEN="..."

# Start the bot
crier run
```

## CLI commands

| Command | Description |
|---------|-------------|
| `crier run` | Start the bot and watcher loop (runs forever) |
| `crier status` | Show bot status and configuration summary |
| `crier draft <owner/repo>` | Generate a one-shot draft for a repo (no Telegram, no posting) |
| `crier drafts` | List recent drafts, with optional `--status` and `--platform` filters |
| `crier history` | Show posting history |

## Configuration

All settings can be set via `CRIER_`-prefixed environment variables or a `.env` file:

| Variable | Description | Default |
|----------|-------------|---------|
| `CRIER_TELEGRAM_BOT_TOKEN` | Telegram bot token | (required) |
| `CRIER_TELEGRAM_OWNER_ID` | Telegram user ID for approvals | (required) |
| `CRIER_REPOS` | Comma-separated `owner/repo` list | (required) |
| `CRIER_GITHUB_TOKEN` | GitHub API token (higher rate limits) | `$GH_TOKEN` |
| `CRIER_ANTHROPIC_API_KEY` | Anthropic API key | `$ANTHROPIC_API_KEY` |
| `CRIER_POLL_INTERVAL` | Seconds between polls | `60` |
| `CRIER_INTEREST_THRESHOLD` | Minimum score (0-10) to generate drafts | `5.0` |
| `CRIER_DRY_RUN` | Skip actual posting | `false` |

## Telegram bot commands

Once the bot is running, these commands are available in Telegram:

- `/start` -- Show help
- `/status` -- Bot status and pending draft count
- `/history` -- Recent posting history
- `/draft <owner/repo>` -- Manually trigger a draft

Draft messages include inline buttons to **Approve**, **Edit**, or **Reject**, plus per-platform toggles (Twitter, Discord, LinkedIn).

## Tests

132 tests covering all modules:

```bash
pip install ".[dev]"
pytest
```

## License

MIT -- see [LICENSE](LICENSE).


---

*[View on GitHub](https://github.com/OpenAdaptAI/openadapt-crier) | [Report an issue](https://github.com/OpenAdaptAI/openadapt-crier/issues/new)*
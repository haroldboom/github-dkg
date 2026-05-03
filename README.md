# github-dkg

Ingest GitHub issues, pull requests, and review comments into [DKG v10](https://docs.origintrail.io) Working Memory as Knowledge Assets.

Every issue and PR becomes a queryable, attributable Knowledge Asset in your DKG v10 node. Key decisions can be promoted to Shared Working Memory — making your team's engineering knowledge accessible to agents.

## Demo

- **Walkthrough notebook:** [`demo.ipynb`](demo.ipynb) — runs end-to-end against a built-in mock of GitHub and the DKG node, no tokens required. Open in [Colab](https://colab.research.google.com/github/spangers11/github-dkg/blob/master/demo.ipynb).
- **Live recording script:** [`examples/demo_video.py`](examples/demo_video.py) — drives all three demos against a real DKG node and the GitHub API; this is the script behind the bounty walkthrough video.

## Install

```bash
pip install github-dkg
```

## Quickstart

```bash
export DKG_TOKEN=your-dkg-token
export DKG_BASE_URL=http://localhost:9200
export DKG_CONTEXT_GRAPH=your-context-graph-id
export GITHUB_TOKEN=your-github-token

# Bulk-ingest all issues and PRs from a repository
github-dkg ingest owner/repo --context-graph $DKG_CONTEXT_GRAPH

# Ingest a single issue
github-dkg ingest-one owner/repo 42 --type issue --context-graph $DKG_CONTEXT_GRAPH

# Ingest a single PR
github-dkg ingest-one owner/repo 99 --type pr --context-graph $DKG_CONTEXT_GRAPH

# Search ingested knowledge
github-dkg search "authentication bug" --context-graph $DKG_CONTEXT_GRAPH

# Promote a Working Memory asset to Shared Working Memory (SHARE)
github-dkg promote dkg://wm/turn/abc123 --context-graph $DKG_CONTEXT_GRAPH
```

## GitHub Action

Automatically ingest issues and PRs as they are created or updated. Add to `.github/workflows/dkg-ingest.yml`:

```yaml
on:
  issues:
    types: [opened, edited, closed]
  pull_request:
    types: [opened, edited, closed]
  pull_request_review:
    types: [submitted]

jobs:
  ingest:
    runs-on: ubuntu-latest
    steps:
      - uses: spangers11/github-dkg@v0.1.0
        id: ingest
        with:
          dkg-token: ${{ secrets.DKG_TOKEN }}
          dkg-base-url: ${{ secrets.DKG_BASE_URL }}
          dkg-context-graph: ${{ secrets.DKG_CONTEXT_GRAPH }}
```

See `examples/workflow.yml` for a complete example including automatic promotion of architecture-decision PRs to Shared Working Memory.

## Python API

```python
import asyncio
from github_dkg import DKGClient, GitHubClient, GitHubDKGIngestor

async def main():
    dkg = DKGClient(base_url="http://localhost:9200", token="your-token")
    gh = GitHubClient(token="your-github-token")
    ingestor = GitHubDKGIngestor(dkg=dkg, github=gh, context_graph_id="cg-123")

    # Bulk ingest
    result = await ingestor.ingest_repo("owner", "repo", since="2024-01-01")
    print(f"Ingested {result.total} items ({len(result.errors)} errors)")

    # Single item
    resp = await ingestor.ingest_issue("owner", "repo", 42)
    print(f"Turn URI: {resp['turnUri']}")

    # Promote to Shared Working Memory
    await ingestor.promote(resp["turnUri"])

asyncio.run(main())
```

## `--since` filtering

`--since` accepts an ISO 8601 timestamp and limits ingest to items updated after that point.

- **Issues:** filtered server-side by GitHub via the `since` parameter on `/issues`.
- **Pull requests:** GitHub's `/pulls` endpoint has no `since` filter, so the package requests `sort=updated&direction=desc` and stops paginating once results fall below the cutoff. Net result: only PRs touched after `--since` are fetched and ingested.

Comment-only updates (a new comment without an issue/PR body edit) still bump `updated_at`, so they're included.

## Rate limiting

`GitHubClient` raises `github_dkg.github_client.GitHubRateLimitError` when GitHub returns `403`/`429` with `X-RateLimit-Remaining: 0`. The exception carries `reset_at` (unix timestamp) so callers can decide whether to back off, sleep, or fail. Authenticated tokens get 5,000 requests/hour; bulk-ingesting a large repo with many comment-heavy PRs can approach this limit.

```python
from github_dkg.github_client import GitHubRateLimitError

try:
    result = await ingestor.ingest_repo("OriginTrail", "dkg-v9")
except GitHubRateLimitError as e:
    print(f"Rate limited; resets at unix={e.reset_at}")
```

## Memory layers

| Layer | Flag | Visibility |
|---|---|---|
| Working Memory | `--layer wm` (default) | Private to your node |
| Shared Working Memory | `--layer swm` | Gossiped across the paranet |

Promotion from Working Memory to Shared Working Memory is always explicit — nothing is shared automatically.

## License

MIT

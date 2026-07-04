# github-dkg

[![CI](https://github.com/haroldboom/github-dkg/actions/workflows/ci.yml/badge.svg)](https://github.com/haroldboom/github-dkg/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/github-dkg.svg)](https://pypi.org/project/github-dkg/)
[![Python](https://img.shields.io/pypi/pyversions/github-dkg.svg)](https://pypi.org/project/github-dkg/)
[![License](https://img.shields.io/pypi/l/github-dkg.svg)](https://github.com/haroldboom/github-dkg/blob/master/LICENSE)

Ingest GitHub issues, pull requests, and review comments into [DKG v10](https://docs.origintrail.io) Working Memory as Knowledge Assets.

Every issue and PR becomes a queryable, attributable Knowledge Asset in your DKG v10 node. Key decisions can be promoted to Shared Working Memory — making your team's engineering knowledge accessible to agents.

## Demo

- **Walkthrough video:** [youtu.be/pfICj9VR1gE](https://youtu.be/pfICj9VR1gE) — narrated run of all three demos against a live DKG v10 node.
- **Walkthrough notebook:** [`demo.ipynb`](demo.ipynb) — runs end-to-end against a built-in mock of GitHub and the DKG node, no tokens required. Open in [Colab](https://colab.research.google.com/github/haroldboom/github-dkg/blob/master/demo.ipynb).
- **Live recording script:** [`examples/demo_video.py`](examples/demo_video.py) — drives all three demos against a real DKG node and the GitHub API; this is the script behind the walkthrough video.

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

# Create the Context Graph on the node — it must exist before ingesting into it
github-dkg create-context-graph my-repo-knowledge --id $DKG_CONTEXT_GRAPH

# Bulk-ingest all issues and PRs from a repository
github-dkg ingest owner/repo --context-graph $DKG_CONTEXT_GRAPH

# Ingest a single issue
github-dkg ingest-one owner/repo 42 --type issue --context-graph $DKG_CONTEXT_GRAPH

# Ingest a single PR
github-dkg ingest-one owner/repo 99 --type pr --context-graph $DKG_CONTEXT_GRAPH

# Search ingested knowledge (searches layers wm,swm by default; override with --layers)
github-dkg search "authentication bug" --context-graph $DKG_CONTEXT_GRAPH --layers wm,swm

# Promote a Working Memory asset to Shared Working Memory (SHARE)
github-dkg promote dkg://wm/turn/abc123 --context-graph $DKG_CONTEXT_GRAPH
```

Every command that takes `--context-graph` also reads it from the `DKG_CONTEXT_GRAPH` environment variable, so you can set it once and omit the flag. The Context Graph must already exist on the node (create it with `create-context-graph`) before ingesting into it.

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
      - uses: haroldboom/github-dkg@v0.1.5
        id: ingest
        with:
          dkg-token: ${{ secrets.DKG_TOKEN }}
          dkg-base-url: ${{ secrets.DKG_BASE_URL }}
          dkg-context-graph: ${{ secrets.DKG_CONTEXT_GRAPH }}
```

See `examples/workflow.yml` for a complete example including automatic promotion of architecture-decision PRs to Shared Working Memory.

> **Note:** the runner executing the Action must have network access to your DKG node — use a self-hosted runner on the node's network, or a publicly reachable node URL. A `localhost` DKG node is not reachable from GitHub-hosted runners.

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

| Layer | Write flag | Search flag | Visibility |
|---|---|---|---|
| Working Memory | `--layer wm` (default) | `--layers wm` | Private to your node |
| Shared Working Memory | `--layer swm` | `--layers swm` | Gossiped across the paranet |

`search --layers` takes a comma-separated list and defaults to `wm,swm` (both layers). The layers are always sent explicitly because newer node builds return zero results when `memoryLayers` is omitted; the Python API (`DKGClient.memory_search`) applies the same `["wm", "swm"]` default when `memory_layers` is `None`.

Promotion from Working Memory to Shared Working Memory is always explicit — nothing is shared automatically.

## Node compatibility

Verified against DKG v10 node build `10.0.2` (July 2026). Notably, promotion on current nodes is an **async job**: the client submits `POST /api/knowledge-assets/{name}/swm/share-async` and polls the job until it succeeds or fails (older builds fall back to the legacy `/api/assertion/{name}/promote-async` routes; the old synchronous `/promote` route is gone). `DKGClient.assertion_promote` / `github-dkg promote` handle this transparently and return the final job view.

Also note: on node `10.0.2`, `/api/query` (raw SPARQL) cannot see Working Memory (`wm`) quads — the RFC-29 isolation gate is fail-closed in this build. `github-dkg search` uses `memory/search`, which **does** return `wm` results, so search is unaffected.

## License

MIT

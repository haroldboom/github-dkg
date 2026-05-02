# github-dkg

Ingest GitHub issues, pull requests, and review comments into [DKG v10](https://docs.origintrail.io) Working Memory as Knowledge Assets.

Every issue and PR becomes a queryable, attributable Knowledge Asset in your DKG v10 node. Key decisions can be promoted to Shared Working Memory — making your team's engineering knowledge accessible to agents.

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
github-dkg search owner/repo "authentication bug" --context-graph $DKG_CONTEXT_GRAPH

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
      - uses: your-org/github-dkg@v0.1.0
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

## Memory layers

| Layer | Flag | Visibility |
|---|---|---|
| Working Memory | `--layer wm` (default) | Private to your node |
| Shared Working Memory | `--layer swm` | Gossiped across the paranet |

Promotion from Working Memory to Shared Working Memory is always explicit — nothing is shared automatically.

## License

MIT

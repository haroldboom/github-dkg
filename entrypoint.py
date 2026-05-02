"""Docker entrypoint for the GitHub Action.

Reads event context from environment variables and ingests the triggering
issue or PR into DKG v10 Working Memory.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys


async def main() -> None:
    from github_dkg.client import DKGClient
    from github_dkg.github_client import GitHubClient
    from github_dkg.ingestor import GitHubDKGIngestor

    dkg_token = os.environ.get("DKG_TOKEN", "")
    dkg_url = os.environ.get("DKG_BASE_URL", "http://localhost:9200")
    context_graph = os.environ.get("DKG_CONTEXT_GRAPH", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    layer = os.environ.get("INPUT_LAYER", "wm")
    repo_full = os.environ.get("GITHUB_REPOSITORY", "")  # owner/repo
    event_name = os.environ.get("INPUT_EVENT_TYPE") or os.environ.get("GITHUB_EVENT_NAME", "")
    item_number_env = os.environ.get("INPUT_ITEM_NUMBER", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")

    if not repo_full or "/" not in repo_full:
        _fail("GITHUB_REPOSITORY not set or invalid")
    owner, repo = repo_full.split("/", 1)

    # Resolve item number from event payload if not explicitly provided
    item_number: int | None = int(item_number_env) if item_number_env else None
    if item_number is None and event_path:
        try:
            with open(event_path) as f:
                payload = json.load(f)
            item_number = (
                payload.get("issue", {}).get("number")
                or payload.get("pull_request", {}).get("number")
            )
        except Exception:
            pass

    if item_number is None:
        _fail("Could not determine issue/PR number from event payload. Set input.item-number explicitly.")

    dkg = DKGClient(base_url=dkg_url, token=dkg_token)
    gh = GitHubClient(token=github_token)
    ingestor = GitHubDKGIngestor(dkg=dkg, github=gh, context_graph_id=context_graph, layer=layer)

    is_pr = event_name in ("pull_request", "pull_request_review")

    if is_pr:
        resp = await ingestor.ingest_pull(owner, repo, item_number)
    else:
        resp = await ingestor.ingest_issue(owner, repo, item_number)

    turn_uri = resp.get("turnUri", "")
    result_layer = resp.get("layer", layer)

    # Write GitHub Actions outputs
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"turn-uri={turn_uri}\n")
            f.write(f"layer={result_layer}\n")

    print(f"Ingested: {turn_uri} (layer={result_layer})")


def _fail(msg: str) -> None:
    print(f"::error::{msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

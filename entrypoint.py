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
    dkg_url = os.environ.get("DKG_BASE_URL", "")
    context_graph = os.environ.get("DKG_CONTEXT_GRAPH", "")
    github_token = os.environ.get("GITHUB_TOKEN", "")
    layer = os.environ.get("INPUT_LAYER", "wm")

    missing = [
        env_name
        for env_name, value in (
            ("DKG_TOKEN", dkg_token),
            ("GITHUB_TOKEN", github_token),
            ("DKG_CONTEXT_GRAPH", context_graph),
            ("DKG_BASE_URL", dkg_url),
        )
        if not value
    ]
    if missing:
        _fail(f"Missing required environment variable(s): {', '.join(missing)}")

    repo_full = os.environ.get("GITHUB_REPOSITORY", "")  # owner/repo
    event_name = os.environ.get("INPUT_EVENT_TYPE") or os.environ.get("GITHUB_EVENT_NAME", "")
    item_number_env = os.environ.get("INPUT_ITEM_NUMBER", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")

    if not repo_full or "/" not in repo_full:
        _fail("GITHUB_REPOSITORY not set or invalid")
    owner, repo = repo_full.split("/", 1)

    payload: dict = {}
    if event_path:
        try:
            with open(event_path) as f:
                payload = json.load(f)
        except Exception:
            payload = {}

    # Resolve item number from event payload if not explicitly provided
    item_number: int | None = None
    if item_number_env:
        try:
            item_number = int(item_number_env)
        except ValueError:
            _fail(f"INPUT_ITEM_NUMBER is not a valid integer: {item_number_env!r}")
    if item_number is None:
        item_number = (
            payload.get("issue", {}).get("number")
            or payload.get("pull_request", {}).get("number")
        )

    if item_number is None:
        _fail("Could not determine issue/PR number from event payload. Set input.item-number explicitly.")

    dkg = DKGClient(base_url=dkg_url, token=dkg_token)
    gh = GitHubClient(token=github_token)
    ingestor = GitHubDKGIngestor(dkg=dkg, github=gh, context_graph_id=context_graph, layer=layer)

    # Route to PR ingest for any event whose payload describes a PR. This catches
    # pull_request, pull_request_target, pull_request_review, and the
    # issue_comment events that fire on PRs (payload.issue.pull_request set).
    is_pr = (
        event_name in ("pull_request", "pull_request_target", "pull_request_review")
        or "pull_request" in payload
        or "pull_request" in payload.get("issue", {})
    )

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

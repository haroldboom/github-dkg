"""github-dkg demo recording script.

Runs the three demos that make up the bounty walkthrough video:
  1. Ingest a single issue + a single PR — show the formatted Markdown asset
  2. Bulk ingest a small repository with `since=` filtering
  3. Semantic search across the ingested Knowledge Assets

Usage:
    export DKG_TOKEN=$(dkg auth show)
    export GITHUB_TOKEN=ghp_...
    export DEMO_REPO=OriginTrail/dkg-integrations  # optional; small repo recommended
    python examples/demo_video.py
"""

from __future__ import annotations

import asyncio
import os
import time

from github_dkg import DKGClient, GitHubClient, GitHubDKGIngestor
from github_dkg.formatter import format_issue, format_pull_request


DKG_TOKEN = os.environ.get("DKG_TOKEN", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
DEMO_REPO = os.environ.get("DEMO_REPO", "OriginTrail/dkg-integrations")
CONTEXT_GRAPH = os.environ.get("DKG_CONTEXT_GRAPH", "github-dkg-demo")


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def banner(title: str) -> None:
    w = 62
    print("\n" + "═" * w)
    pad = (w - 2 - len(title)) // 2
    print(" " * pad + " " + title)
    print("═" * w + "\n")


def step(msg: str) -> None:    print(f"  ►  {msg}")
def ok(msg: str) -> None:      print(f"  ✓  {msg}")
def info(msg: str) -> None:    print(f"     {msg}")
def pause(s: float = 1.0) -> None: time.sleep(s)


# ──────────────────────────────────────────────────────────────────
# Demo 1 — single-item ingest
# ──────────────────────────────────────────────────────────────────

async def demo_single_item(ingestor: GitHubDKGIngestor, gh: GitHubClient) -> str | None:
    banner("DEMO 1  —  Ingest one issue and one PR")
    print("  Each GitHub item becomes one Knowledge Asset (one /api/memory/turn).\n")
    pause()

    owner, repo = DEMO_REPO.split("/", 1)
    last_uri: str | None = None

    # Pick the lowest-numbered open or closed issue / PR for a stable demo
    step(f"Looking up the first issue in {DEMO_REPO} ...")
    pause(0.4)
    issue_number: int | None = None
    async for issue in gh.list_issues(owner, repo, state="all"):
        issue_number = issue["number"]
        break

    if issue_number is not None:
        step(f"Ingesting issue #{issue_number} ...")
        pause(0.4)
        issue = await gh.get_issue(owner, repo, issue_number)
        comments = await gh.list_issue_comments(owner, repo, issue_number)
        markdown = format_issue(issue, comments[:5], owner, repo)
        info("Formatted Markdown sent to /api/memory/turn:")
        print()
        for line in markdown.splitlines()[:10]:
            print(f"      {line}")
        print("      ...")
        print()
        pause(0.6)
        resp = await ingestor.ingest_issue(owner, repo, issue_number)
        last_uri = resp.get("turnUri")
        ok(f"UAL    : {last_uri}")
        info(f"Quads  : {resp.get('totalQuads', '?')}  |  layer: {resp.get('layer')}")
        print()
        pause(0.7)

    step(f"Looking up the first PR in {DEMO_REPO} ...")
    pause(0.4)
    pr_number: int | None = None
    async for pr in gh.list_pulls(owner, repo, state="all"):
        pr_number = pr["number"]
        break

    if pr_number is not None:
        step(f"Ingesting PR #{pr_number} ...")
        pause(0.4)
        resp = await ingestor.ingest_pull(owner, repo, pr_number)
        last_uri = resp.get("turnUri")
        ok(f"UAL    : {last_uri}")
        info(f"Quads  : {resp.get('totalQuads', '?')}  |  layer: {resp.get('layer')}")
        print()

    return last_uri


# ──────────────────────────────────────────────────────────────────
# Demo 2 — bulk ingest with --since
# ──────────────────────────────────────────────────────────────────

async def demo_bulk(ingestor: GitHubDKGIngestor) -> None:
    banner("DEMO 2  —  Bulk ingest with --since")
    print("  Pulls every issue+PR updated in the last 90 days, semaphore-throttled.\n")
    pause()

    import datetime as _dt
    since = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=90)).isoformat()
    info(f"Repository : {DEMO_REPO}")
    info(f"Since      : {since}")
    info(f"Concurrency: 5\n")
    pause(0.7)

    owner, repo = DEMO_REPO.split("/", 1)
    step("Streaming issues + PRs and writing each turn to DKG ...")
    pause(0.5)
    result = await ingestor.ingest_repo(owner=owner, repo=repo, since=since)

    ok(f"Ingested  : {result.issues_ingested} issues, {result.pulls_ingested} PRs")
    info(f"UALs      : {len(result.turn_uris)} captured")
    if result.errors:
        info(f"Errors    : {len(result.errors)} (printed below)")
        for err in result.errors[:5]:
            print(f"      {err}")
    else:
        info("Errors    : 0")
    print()


# ──────────────────────────────────────────────────────────────────
# Demo 3 — search
# ──────────────────────────────────────────────────────────────────

async def demo_search(dkg: DKGClient) -> None:
    banner("DEMO 3  —  Search the ingested knowledge")
    print("  Tri-modal retrieval (vector + SPARQL + text) over Working Memory.\n")
    pause()

    queries = ["bug", "review", "context graph"]
    for q in queries:
        step(f'Search: "{q}"')
        pause(0.3)
        result = await dkg.memory_search(
            context_graph_id=CONTEXT_GRAPH, query=q, limit=3,
        )
        count = result.get("resultCount", 0)
        ok(f"{count} result(s)")
        for item in result.get("results", [])[:3]:
            sim = item.get("similarity", 0.0)
            label = item.get("label") or item.get("entityUri", "")
            print(f"      [{sim:.2f}]  {label[:72]}")
        print()
        pause(0.5)


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

async def main() -> None:
    w = 62
    print("\n╔" + "═" * w + "╗")
    print("║  github-dkg  —  GitHub knowledge → DKG v10 Working Memory ║")
    print("║  Bounty tag: cfi-dkgv10-r1   |   pip install github-dkg   ║")
    print("╚" + "═" * w + "╝\n")

    if not DKG_TOKEN:
        print("ERROR: DKG_TOKEN not set.  export DKG_TOKEN=$(dkg auth show)")
        return
    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN not set.  export GITHUB_TOKEN=ghp_...")
        return

    step("Connecting to DKG v10 node at http://localhost:9200 ...")
    dkg = DKGClient(token=DKG_TOKEN)
    if not await dkg.ping():
        print("ERROR: DKG node not reachable.  Run: dkg start && sleep 20")
        return
    ok("Connected to DKG v10 node\n")
    pause(0.6)

    gh = GitHubClient(token=GITHUB_TOKEN)
    ingestor = GitHubDKGIngestor(
        dkg=dkg,
        github=gh,
        context_graph_id=CONTEXT_GRAPH,
        layer="wm",
        concurrency=5,
    )

    await demo_single_item(ingestor, gh)
    pause(1.5)
    await demo_bulk(ingestor)
    pause(1.5)
    await demo_search(dkg)

    banner("DEMO COMPLETE")
    print("  All ingested items are now Knowledge Assets in DKG Working Memory.")
    print(f"  Context Graph: {CONTEXT_GRAPH}\n")
    print("  Install :  pip install github-dkg")
    print("  GitHub  :  https://github.com/spangers11/github-dkg")
    print("  Bounty  :  cfi-dkgv10-r1")
    print()


if __name__ == "__main__":
    asyncio.run(main())

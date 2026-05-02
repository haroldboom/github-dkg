"""CLI entry point for github-dkg."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from .client import DKGClient
from .github_client import GitHubClient
from .ingestor import GitHubDKGIngestor


def _make_clients(
    dkg_token: str | None,
    dkg_url: str | None,
    github_token: str | None,
) -> tuple[DKGClient, GitHubClient]:
    dkg = DKGClient(base_url=dkg_url, token=dkg_token)
    gh = GitHubClient(token=github_token)
    return dkg, gh


@click.group()
def main() -> None:
    """github-dkg: Ingest GitHub knowledge into DKG v10 Working Memory."""


@main.command()
@click.argument("repo")  # owner/repo
@click.option("--context-graph", required=True, envvar="DKG_CONTEXT_GRAPH", help="Context Graph ID")
@click.option("--layer", default="wm", show_default=True, type=click.Choice(["wm", "swm"]))
@click.option("--since", default=None, help="ISO 8601 date — only ingest items updated after this date")
@click.option("--no-issues", is_flag=True, default=False, help="Skip issues")
@click.option("--no-pulls", is_flag=True, default=False, help="Skip pull requests")
@click.option("--concurrency", default=5, show_default=True, help="Parallel DKG writes")
@click.option("--dkg-token", envvar="DKG_TOKEN", default=None)
@click.option("--dkg-url", envvar="DKG_BASE_URL", default=None)
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
def ingest(
    repo: str,
    context_graph: str,
    layer: str,
    since: str | None,
    no_issues: bool,
    no_pulls: bool,
    concurrency: int,
    dkg_token: str | None,
    dkg_url: str | None,
    github_token: str | None,
) -> None:
    """Bulk-ingest all issues and PRs from OWNER/REPO into Working Memory."""
    if "/" not in repo:
        click.echo("Error: REPO must be in owner/repo format", err=True)
        sys.exit(1)
    owner, repo_name = repo.split("/", 1)

    async def run() -> None:
        dkg, gh = _make_clients(dkg_token, dkg_url, github_token)

        click.echo(f"Connecting to DKG node...")
        if not await dkg.ping():
            click.echo("Error: DKG node unreachable or token invalid", err=True)
            sys.exit(1)

        ingestor = GitHubDKGIngestor(
            dkg=dkg,
            github=gh,
            context_graph_id=context_graph,
            layer=layer,
            concurrency=concurrency,
        )

        click.echo(f"Ingesting {owner}/{repo_name} → context graph '{context_graph}' (layer={layer})")
        result = await ingestor.ingest_repo(
            owner=owner,
            repo=repo_name,
            since=since,
            include_issues=not no_issues,
            include_pulls=not no_pulls,
        )

        click.echo(f"Done: {result.issues_ingested} issues, {result.pulls_ingested} PRs ingested")
        if result.errors:
            click.echo(f"Errors ({len(result.errors)}):")
            for err in result.errors:
                click.echo(f"  {err}", err=True)

    asyncio.run(run())


@main.command()
@click.argument("repo")  # owner/repo
@click.argument("number", type=int)
@click.option("--type", "item_type", required=True, type=click.Choice(["issue", "pr"]))
@click.option("--context-graph", required=True, envvar="DKG_CONTEXT_GRAPH")
@click.option("--layer", default="wm", show_default=True, type=click.Choice(["wm", "swm"]))
@click.option("--dkg-token", envvar="DKG_TOKEN", default=None)
@click.option("--dkg-url", envvar="DKG_BASE_URL", default=None)
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
def ingest_one(
    repo: str,
    number: int,
    item_type: str,
    context_graph: str,
    layer: str,
    dkg_token: str | None,
    dkg_url: str | None,
    github_token: str | None,
) -> None:
    """Ingest a single issue or PR by number."""
    if "/" not in repo:
        click.echo("Error: REPO must be in owner/repo format", err=True)
        sys.exit(1)
    owner, repo_name = repo.split("/", 1)

    async def run() -> None:
        dkg, gh = _make_clients(dkg_token, dkg_url, github_token)
        ingestor = GitHubDKGIngestor(dkg=dkg, github=gh, context_graph_id=context_graph, layer=layer)
        if item_type == "issue":
            resp = await ingestor.ingest_issue(owner, repo_name, number)
        else:
            resp = await ingestor.ingest_pull(owner, repo_name, number)
        turn_uri = resp.get("turnUri", "")
        click.echo(f"Ingested: {turn_uri}")

    asyncio.run(run())


@main.command()
@click.argument("turn-uri")
@click.option("--context-graph", required=True, envvar="DKG_CONTEXT_GRAPH")
@click.option("--dkg-token", envvar="DKG_TOKEN", default=None)
@click.option("--dkg-url", envvar="DKG_BASE_URL", default=None)
def promote(
    turn_uri: str,
    context_graph: str,
    dkg_token: str | None,
    dkg_url: str | None,
) -> None:
    """Promote a Working Memory Knowledge Asset to Shared Working Memory (SHARE)."""

    async def run() -> None:
        dkg = DKGClient(base_url=dkg_url, token=dkg_token)
        ingestor = GitHubDKGIngestor(
            dkg=dkg,
            github=GitHubClient(token=os.environ.get("GITHUB_TOKEN", "placeholder")),
            context_graph_id=context_graph,
        )
        resp = await ingestor.promote(turn_uri)
        click.echo(f"Promoted: {resp}")

    asyncio.run(run())


@main.command()
@click.argument("repo")
@click.argument("query")
@click.option("--context-graph", required=True, envvar="DKG_CONTEXT_GRAPH")
@click.option("--limit", default=10, show_default=True)
@click.option("--dkg-token", envvar="DKG_TOKEN", default=None)
@click.option("--dkg-url", envvar="DKG_BASE_URL", default=None)
def search(
    repo: str,
    query: str,
    context_graph: str,
    limit: int,
    dkg_token: str | None,
    dkg_url: str | None,
) -> None:
    """Search ingested GitHub knowledge in Working Memory."""

    async def run() -> None:
        dkg = DKGClient(base_url=dkg_url, token=dkg_token)
        result = await dkg.memory_search(
            context_graph_id=context_graph,
            query=query,
            limit=limit,
        )
        count = result.get("resultCount", 0)
        click.echo(f"{count} result(s) for '{query}':")
        for item in result.get("results", []):
            label = item.get("label", item.get("entityUri", ""))
            snippet = item.get("snippet", "")
            layer = item.get("memoryLayer", "")
            click.echo(f"  [{layer}] {label}")
            if snippet:
                click.echo(f"    {snippet[:120]}")

    asyncio.run(run())

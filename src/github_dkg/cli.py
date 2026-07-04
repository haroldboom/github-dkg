"""CLI entry point for github-dkg."""

from __future__ import annotations

import asyncio
import re
import sys

import click
import httpx

from .client import (
    TRUST_LEVEL_NAMES,
    DKGClient,
    DKGPromoteError,
    DKGPublishError,
    coerce_trust_level,
)
from .github_client import GitHubClient
from .ingestor import GitHubDKGIngestor

_BARE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize_since(since: str | None) -> str | None:
    """Expand a bare YYYY-MM-DD date to a full ISO 8601 UTC timestamp."""
    if since and _BARE_DATE.match(since):
        return f"{since}T00:00:00Z"
    return since


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
@click.option("--concurrency", default=5, show_default=True, type=click.IntRange(min=1), help="Parallel DKG writes")
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
    since = _normalize_since(since)

    async def run() -> int:
        dkg, gh = _make_clients(dkg_token, dkg_url, github_token)
        try:
            click.echo("Connecting to DKG node...")
            try:
                reachable = await dkg.ping()
            except httpx.HTTPStatusError as exc:
                click.echo(
                    f"Error: DKG node rejected the request "
                    f"(HTTP {exc.response.status_code}) — check DKG_TOKEN",
                    err=True,
                )
                return 1
            if not reachable:
                click.echo("Error: DKG node unreachable — check DKG_BASE_URL", err=True)
                return 1

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
                return 1
            return 0
        finally:
            await dkg.aclose()
            await gh.aclose()

    sys.exit(asyncio.run(run()))


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
        try:
            ingestor = GitHubDKGIngestor(dkg=dkg, github=gh, context_graph_id=context_graph, layer=layer)
            if item_type == "issue":
                resp = await ingestor.ingest_issue(owner, repo_name, number)
            else:
                resp = await ingestor.ingest_pull(owner, repo_name, number)
            turn_uri = resp.get("turnUri", "")
            click.echo(f"Ingested: {turn_uri}")
        finally:
            await dkg.aclose()
            await gh.aclose()

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
        try:
            ingestor = GitHubDKGIngestor(dkg=dkg, context_graph_id=context_graph)
            resp = await ingestor.promote(turn_uri)
            click.echo(f"Promoted: {resp}")
        finally:
            await dkg.aclose()

    asyncio.run(run())


@main.command("create-context-graph")
@click.argument("name")
@click.option("--id", "graph_id", default=None, help="Explicit Context Graph ID (defaults to NAME)")
@click.option("--dkg-token", envvar="DKG_TOKEN", default=None)
@click.option("--dkg-url", envvar="DKG_BASE_URL", default=None)
def create_context_graph(
    name: str,
    graph_id: str | None,
    dkg_token: str | None,
    dkg_url: str | None,
) -> None:
    """Create a Context Graph on the DKG node (must exist before ingest)."""

    async def run() -> None:
        dkg = DKGClient(base_url=dkg_url, token=dkg_token)
        try:
            resp = await dkg.create_context_graph(name, id=graph_id)
            created = resp.get("created", graph_id or name)
            uri = resp.get("uri", "")
            click.echo(f"Created context graph: {created}" + (f" ({uri})" if uri else ""))
        finally:
            await dkg.aclose()

    asyncio.run(run())


@main.command()
@click.argument("query")
@click.option("--context-graph", required=True, envvar="DKG_CONTEXT_GRAPH")
@click.option("--limit", default=10, show_default=True)
@click.option(
    "--layers",
    default="wm,swm",
    show_default=True,
    help="Comma-separated memory layers to search. Newer node builds return "
    "nothing when memoryLayers is omitted, so layers are always sent.",
)
@click.option("--dkg-token", envvar="DKG_TOKEN", default=None)
@click.option("--dkg-url", envvar="DKG_BASE_URL", default=None)
def search(
    query: str,
    context_graph: str,
    limit: int,
    layers: str,
    dkg_token: str | None,
    dkg_url: str | None,
) -> None:
    """Search ingested GitHub knowledge in Working Memory."""

    async def run() -> None:
        dkg = DKGClient(base_url=dkg_url, token=dkg_token)
        try:
            result = await dkg.memory_search(
                context_graph_id=context_graph,
                query=query,
                limit=limit,
                memory_layers=[layer.strip() for layer in layers.split(",") if layer.strip()],
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
        finally:
            await dkg.aclose()

    asyncio.run(run())


@main.command("publish-decision")
@click.argument("repo")  # owner/repo
@click.argument("number", type=int)
@click.option("--type", "item_type", required=True, type=click.Choice(["issue", "pr"]))
@click.option("--context-graph", required=True, envvar="DKG_CONTEXT_GRAPH")
@click.option("--epochs", default=None, type=int, help="On-chain publish epochs (node default when omitted)")
@click.option("--dkg-token", envvar="DKG_TOKEN", default=None)
@click.option("--dkg-url", envvar="DKG_BASE_URL", default=None)
@click.option("--github-token", envvar="GITHUB_TOKEN", default=None)
def publish_decision(
    repo: str,
    number: int,
    item_type: str,
    context_graph: str,
    epochs: int | None,
    dkg_token: str | None,
    dkg_url: str | None,
    github_token: str | None,
) -> None:
    """Publish an issue or PR as a decision to Verifiable Memory (on-chain)."""
    if "/" not in repo:
        click.echo("Error: REPO must be in owner/repo format", err=True)
        sys.exit(1)
    owner, repo_name = repo.split("/", 1)

    async def run() -> int:
        dkg, gh = _make_clients(dkg_token, dkg_url, github_token)
        try:
            ingestor = GitHubDKGIngestor(dkg=dkg, github=gh, context_graph_id=context_graph)
            try:
                result = await ingestor.publish_decision(
                    owner, repo_name, number, kind=item_type, publish_epochs=epochs
                )
            except (DKGPublishError, DKGPromoteError) as exc:
                click.echo(f"Error: {exc}", err=True)
                return 1
            status = result.get("status", "")
            click.echo(f"Published {item_type} #{number} ({status}):")
            click.echo(f"  UAL:        {result.get('ual', '')}")
            click.echo(f"  txHash:     {result.get('txHash', '')}")
            click.echo(f"  merkleRoot: {result.get('merkleRoot', '')}")
            return 0
        finally:
            await dkg.aclose()
            await gh.aclose()

    sys.exit(asyncio.run(run()))


@main.command()
@click.argument("ual")
@click.option("--context-graph", required=True, envvar="DKG_CONTEXT_GRAPH")
@click.option("--dkg-token", envvar="DKG_TOKEN", default=None)
@click.option("--dkg-url", envvar="DKG_BASE_URL", default=None)
def endorse(
    ual: str,
    context_graph: str,
    dkg_token: str | None,
    dkg_url: str | None,
) -> None:
    """Endorse a published Knowledge Asset by UAL (trust level → Endorsed)."""

    async def run() -> None:
        dkg = DKGClient(base_url=dkg_url, token=dkg_token)
        try:
            resp = await dkg.endorse(context_graph_id=context_graph, ual=ual)
            click.echo(f"Endorsed {ual}")
            click.echo("Endorsement triples ride the next publish batch.")
            if resp:
                click.echo(f"  {resp}")
        finally:
            await dkg.aclose()

    asyncio.run(run())


@main.command("verify-decision")
@click.argument("vm-id")
@click.argument("batch-id")
@click.option("--context-graph", required=True, envvar="DKG_CONTEXT_GRAPH")
@click.option("--required-signatures", default=None, type=int, help="Signature quorum to request")
@click.option("--dkg-token", envvar="DKG_TOKEN", default=None)
@click.option("--dkg-url", envvar="DKG_BASE_URL", default=None)
def verify_decision(
    vm_id: str,
    batch_id: str,
    context_graph: str,
    required_signatures: int | None,
    dkg_token: str | None,
    dkg_url: str | None,
) -> None:
    """Request network verification of a Verifiable Memory batch."""

    async def run() -> int:
        dkg = DKGClient(base_url=dkg_url, token=dkg_token)
        try:
            result = await dkg.request_verification(
                context_graph_id=context_graph,
                verifiable_memory_id=vm_id,
                batch_id=batch_id,
                required_signatures=required_signatures,
            )
            status = result.get("status", "unknown")
            click.echo(f"Verification status: {status}")
            signatures = result.get("signatures")
            count = (
                len(signatures)
                if isinstance(signatures, list)
                else result.get("signatureCount", result.get("signerCount"))
            )
            if count is not None:
                click.echo(f"Signers: {count}")
            return 0 if status == "verified" else 1
        finally:
            await dkg.aclose()

    sys.exit(asyncio.run(run()))


def _sparql_escape(term: str) -> str:
    """Escape a literal for embedding in a double-quoted SPARQL string."""
    return term.replace("\\", "\\\\").replace('"', '\\"')


def _oracle_rows(result: dict) -> list[dict]:
    """Extract binding rows from a /api/query response.

    Handles both the W3C SPARQL JSON shape ({"results": {"bindings": [...]}})
    and flat {"results": [...]} / {"bindings": [...]} variants.
    """
    results = result.get("results")
    if isinstance(results, dict):
        bindings = results.get("bindings", [])
    elif isinstance(results, list):
        bindings = results
    else:
        bindings = result.get("bindings", [])
    return [row for row in bindings if isinstance(row, dict)]


@main.command()
@click.argument("query")
@click.option("--context-graph", required=True, envvar="DKG_CONTEXT_GRAPH")
@click.option(
    "--min-trust",
    default="0",
    show_default=True,
    help="Minimum trust level: 0-3 or a name (selfAttested, endorsed, "
    "partiallyVerified, consensusVerified)",
)
@click.option("--limit", default=10, show_default=True)
@click.option("--dkg-token", envvar="DKG_TOKEN", default=None)
@click.option("--dkg-url", envvar="DKG_BASE_URL", default=None)
def oracle(
    query: str,
    context_graph: str,
    min_trust: str,
    limit: int,
    dkg_token: str | None,
    dkg_url: str | None,
) -> None:
    """Query the Verifiable Memory trust-gradient surface (knowledge oracle)."""
    try:
        trust = coerce_trust_level(min_trust)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--min-trust") from exc

    term = _sparql_escape(query)
    sparql = (
        "SELECT ?s ?p ?o WHERE { ?s ?p ?o . "
        f'FILTER(isLiteral(?o) && CONTAINS(LCASE(STR(?o)), LCASE("{term}"))) '
        f"}} LIMIT {limit}"
    )

    async def run() -> None:
        dkg = DKGClient(base_url=dkg_url, token=dkg_token)
        try:
            result = await dkg.query(
                sparql,
                context_graph_id=context_graph,
                view="verifiable-memory",
                min_trust=trust,
            )
            rows = _oracle_rows(result)
            click.echo(f"{len(rows)} row(s) for '{query}':")
            for row in rows:
                cells = []
                for var in ("s", "p", "o"):
                    cell = row.get(var, "")
                    if isinstance(cell, dict):  # W3C binding: {"value": ...}
                        cell = cell.get("value", "")
                    cells.append(str(cell))
                click.echo("  " + "  |  ".join(cells))
            click.echo(
                f"-- provenance: contextGraphId={context_graph} "
                f"view=verifiable-memory "
                f"minTrust={trust} ({TRUST_LEVEL_NAMES[trust]})"
            )
        finally:
            await dkg.aclose()

    asyncio.run(run())

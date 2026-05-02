"""Orchestrates fetching from GitHub and writing to DKG v10 Working Memory."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .client import DKGClient
from .formatter import format_issue, format_pull_request
from .github_client import GitHubClient


@dataclass
class IngestResult:
    issues_ingested: int = 0
    pulls_ingested: int = 0
    errors: list[str] = field(default_factory=list)
    turn_uris: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.issues_ingested + self.pulls_ingested


class GitHubDKGIngestor:
    """Fetch GitHub items and write them to DKG v10 Working Memory.

    Each issue and PR becomes one Knowledge Asset (via /api/memory/turn).
    All assets for a repo are scoped to a single Context Graph.
    """

    def __init__(
        self,
        dkg: DKGClient,
        github: GitHubClient,
        context_graph_id: str,
        layer: str = "wm",
        max_comments_per_issue: int = 20,
        max_reviews_per_pr: int = 10,
        concurrency: int = 5,
    ) -> None:
        self._dkg = dkg
        self._gh = github
        self._context_graph_id = context_graph_id
        self._layer = layer
        self._max_comments = max_comments_per_issue
        self._max_reviews = max_reviews_per_pr
        self._sem = asyncio.Semaphore(concurrency)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ingest_repo(
        self,
        owner: str,
        repo: str,
        since: str | None = None,
        include_issues: bool = True,
        include_pulls: bool = True,
    ) -> IngestResult:
        result = IngestResult()
        tasks: list[Any] = []

        if include_issues:
            async for issue in self._gh.list_issues(owner, repo, since=since):
                tasks.append(self._ingest_issue(owner, repo, issue, result))

        if include_pulls:
            async for pr in self._gh.list_pulls(owner, repo):
                tasks.append(self._ingest_pull(owner, repo, pr, result))

        await asyncio.gather(*tasks)
        return result

    async def ingest_issue(
        self, owner: str, repo: str, issue_number: int
    ) -> dict[str, Any]:
        """Ingest a single issue by number. Returns the DKG turn response."""
        async with httpx_get_issue(self._gh, owner, repo, issue_number) as issue:
            comments = await self._gh.list_issue_comments(owner, repo, issue_number)
            markdown = format_issue(issue, comments[: self._max_comments], owner, repo)
            return await self._dkg.memory_turn(
                context_graph_id=self._context_graph_id,
                markdown=markdown,
                layer=self._layer,
                session_uri=_repo_session_uri(owner, repo),
            )

    async def ingest_pull(
        self, owner: str, repo: str, pull_number: int
    ) -> dict[str, Any]:
        """Ingest a single PR by number. Returns the DKG turn response."""
        async with httpx_get_pull(self._gh, owner, repo, pull_number) as pr:
            reviews, inline = await asyncio.gather(
                self._gh.list_pull_reviews(owner, repo, pull_number),
                self._gh.list_pull_comments(owner, repo, pull_number),
            )
            markdown = format_pull_request(
                pr,
                reviews[: self._max_reviews],
                inline,
                owner,
                repo,
            )
            return await self._dkg.memory_turn(
                context_graph_id=self._context_graph_id,
                markdown=markdown,
                layer=self._layer,
                session_uri=_repo_session_uri(owner, repo),
            )

    async def promote(self, turn_uri: str) -> dict[str, Any]:
        """Promote a Working Memory Knowledge Asset to Shared Working Memory (SHARE)."""
        name = turn_uri.split("/")[-1]
        return await self._dkg.assertion_promote(
            name=name,
            context_graph_id=self._context_graph_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ingest_issue(
        self,
        owner: str,
        repo: str,
        issue: dict[str, Any],
        result: IngestResult,
    ) -> None:
        async with self._sem:
            try:
                number = issue["number"]
                comments = await self._gh.list_issue_comments(owner, repo, number)
                markdown = format_issue(
                    issue, comments[: self._max_comments], owner, repo
                )
                resp = await self._dkg.memory_turn(
                    context_graph_id=self._context_graph_id,
                    markdown=markdown,
                    layer=self._layer,
                    session_uri=_repo_session_uri(owner, repo),
                )
                result.issues_ingested += 1
                if uri := resp.get("turnUri"):
                    result.turn_uris.append(uri)
            except Exception as exc:
                result.errors.append(f"issue #{issue.get('number')}: {exc}")

    async def _ingest_pull(
        self,
        owner: str,
        repo: str,
        pr: dict[str, Any],
        result: IngestResult,
    ) -> None:
        async with self._sem:
            try:
                number = pr["number"]
                reviews, inline = await asyncio.gather(
                    self._gh.list_pull_reviews(owner, repo, number),
                    self._gh.list_pull_comments(owner, repo, number),
                )
                markdown = format_pull_request(
                    pr,
                    reviews[: self._max_reviews],
                    inline,
                    owner,
                    repo,
                )
                resp = await self._dkg.memory_turn(
                    context_graph_id=self._context_graph_id,
                    markdown=markdown,
                    layer=self._layer,
                    session_uri=_repo_session_uri(owner, repo),
                )
                result.pulls_ingested += 1
                if uri := resp.get("turnUri"):
                    result.turn_uris.append(uri)
            except Exception as exc:
                result.errors.append(f"PR #{pr.get('number')}: {exc}")


def _repo_session_uri(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


# ---------------------------------------------------------------------------
# Context-manager shims for single-item fetches (used in public ingest_* API)
# These avoid duplicating the GitHub list logic for single-item ingestion.
# ---------------------------------------------------------------------------

class _AsyncContextResult:
    """Wrap a coroutine result as an async context manager."""

    def __init__(self, coro: Any) -> None:
        self._coro = coro
        self._value: Any = None

    async def __aenter__(self) -> Any:
        self._value = await self._coro
        return self._value

    async def __aexit__(self, *_: Any) -> None:
        pass


def httpx_get_issue(gh: GitHubClient, owner: str, repo: str, number: int) -> _AsyncContextResult:
    import httpx as _httpx

    async def _fetch() -> dict[str, Any]:
        async with _httpx.AsyncClient(timeout=gh._timeout) as http:
            r = await http.get(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{number}",
                headers=gh._headers,
            )
            r.raise_for_status()
            return r.json()

    return _AsyncContextResult(_fetch())


def httpx_get_pull(gh: GitHubClient, owner: str, repo: str, number: int) -> _AsyncContextResult:
    import httpx as _httpx

    async def _fetch() -> dict[str, Any]:
        async with _httpx.AsyncClient(timeout=gh._timeout) as http:
            r = await http.get(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}",
                headers=gh._headers,
            )
            r.raise_for_status()
            return r.json()

    return _AsyncContextResult(_fetch())

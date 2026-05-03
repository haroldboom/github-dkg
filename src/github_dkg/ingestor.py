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

    The ``github`` client is optional — required for ingest, but ``promote``
    only touches DKG so it can be omitted there.
    """

    def __init__(
        self,
        dkg: DKGClient,
        github: GitHubClient | None = None,
        context_graph_id: str = "",
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

    def _require_github(self) -> GitHubClient:
        if self._gh is None:
            raise RuntimeError(
                "This operation requires a GitHubClient. "
                "Pass github=... when constructing GitHubDKGIngestor."
            )
        return self._gh

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
        gh = self._require_github()
        result = IngestResult()
        tasks: list[Any] = []

        if include_issues:
            async for issue in gh.list_issues(owner, repo, since=since):
                tasks.append(self._ingest_issue(owner, repo, issue, result))

        if include_pulls:
            async for pr in gh.list_pulls(owner, repo, since=since):
                tasks.append(self._ingest_pull(owner, repo, pr, result))

        await asyncio.gather(*tasks)
        return result

    async def ingest_issue(
        self, owner: str, repo: str, issue_number: int
    ) -> dict[str, Any]:
        """Ingest a single issue by number. Returns the DKG turn response."""
        gh = self._require_github()
        issue = await gh.get_issue(owner, repo, issue_number)
        comments = await gh.list_issue_comments(owner, repo, issue_number)
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
        gh = self._require_github()
        pr, reviews, inline = await asyncio.gather(
            gh.get_pull(owner, repo, pull_number),
            gh.list_pull_reviews(owner, repo, pull_number),
            gh.list_pull_comments(owner, repo, pull_number),
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
        gh = self._require_github()
        async with self._sem:
            try:
                number = issue["number"]
                comments = await gh.list_issue_comments(owner, repo, number)
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
        gh = self._require_github()
        async with self._sem:
            try:
                number = pr["number"]
                reviews, inline = await asyncio.gather(
                    gh.list_pull_reviews(owner, repo, number),
                    gh.list_pull_comments(owner, repo, number),
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

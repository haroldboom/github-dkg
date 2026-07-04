"""Orchestrates fetching from GitHub and writing to DKG v10 Working Memory."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from .client import DKGClient
from .formatter import format_issue, format_pull_request
from .github_client import GitHubClient, GitHubRateLimitError

# Sub-graph names must not contain "/" (the node rejects them); be
# conservative and allow only a safe character set.
_SUB_GRAPH_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_sub_graph_name(raw: str) -> str:
    """Replace "/" and any other disallowed character with "-"."""
    return _SUB_GRAPH_SAFE.sub("-", raw)


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

    Each issue and PR becomes one Knowledge Asset (via /api/memory/turn),
    grouped under a stable per-item sub-graph name
    (``{owner}-{repo}-issue-{n}`` / ``{owner}-{repo}-pr-{n}``). Note this is
    grouping, not idempotency: re-ingesting the same item creates a new
    timestamped version grouped under the same sub-graph — the node does not
    deduplicate. All assets for a repo are scoped to a single Context Graph.

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
        """Bulk-ingest issues and PRs, streaming work as listings arrive.

        Per-item failures are isolated and recorded in ``IngestResult.errors``.
        Two exceptions to that isolation:

        - ``GitHubRateLimitError`` (from listing or any item) is re-raised
          after cancelling in-flight work — continuing would only burn the
          remaining budget.
        - Errors raised by the listing itself propagate.

        In both cases the partially-populated ``IngestResult`` (counts,
        ``turn_uris`` and ``errors`` gathered so far) is attached to the
        raised exception as ``partial_result``.
        """
        gh = self._require_github()
        result = IngestResult()
        tasks: list[asyncio.Task[None]] = []
        try:
            if include_issues:
                async for issue in gh.list_issues(owner, repo, since=since):
                    tasks.append(
                        asyncio.create_task(
                            self._ingest_issue(owner, repo, issue, result)
                        )
                    )
            if include_pulls:
                async for pr in gh.list_pulls(owner, repo, since=since):
                    tasks.append(
                        asyncio.create_task(
                            self._ingest_pull(owner, repo, pr, result)
                        )
                    )
            await asyncio.gather(*tasks)
        except BaseException as exc:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if isinstance(exc, Exception):
                exc.partial_result = result  # type: ignore[attr-defined]
            raise
        return result

    async def ingest_issue(
        self, owner: str, repo: str, issue_number: int
    ) -> dict[str, Any]:
        """Ingest a single issue by number. Returns the DKG turn response.

        Not idempotent: re-ingesting creates a new version grouped under the
        same sub-graph name.
        """
        gh = self._require_github()
        issue = await gh.get_issue(owner, repo, issue_number)
        return await self._write_issue(owner, repo, issue)

    async def ingest_pull(
        self, owner: str, repo: str, pull_number: int
    ) -> dict[str, Any]:
        """Ingest a single PR by number. Returns the DKG turn response.

        Not idempotent: re-ingesting creates a new version grouped under the
        same sub-graph name.
        """
        gh = self._require_github()
        pr = await gh.get_pull(owner, repo, pull_number)
        return await self._write_pull(owner, repo, pr)

    async def promote(self, turn_uri: str) -> dict[str, Any]:
        """Promote a Working Memory Knowledge Asset to Shared Working Memory (SHARE).

        Extracts the assertion name from the last path segment of the URI
        (the whole URI when it contains no "/"); URL-quoting is handled by
        the DKG client. Waits for the node's async promote job to finish.
        """
        name = turn_uri.rsplit("/", 1)[-1] or turn_uri
        return await self._dkg.assertion_promote(
            name=name,
            context_graph_id=self._context_graph_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _write_issue(
        self, owner: str, repo: str, issue: dict[str, Any]
    ) -> dict[str, Any]:
        gh = self._require_github()
        number = issue["number"]
        # Fetch one past the cap so "more exist" is knowable without
        # paginating everything.
        comments = await gh.list_issue_comments(
            owner, repo, number, max_items=self._max_comments + 1
        )
        total = issue.get("comments")
        if not isinstance(total, int):
            total = len(comments)
        markdown = format_issue(
            issue,
            comments[: self._max_comments],
            owner,
            repo,
            total_comments=total,
        )
        return await self._dkg.memory_turn(
            context_graph_id=self._context_graph_id,
            markdown=markdown,
            layer=self._layer,
            session_uri=_repo_session_uri(owner, repo),
            sub_graph_name=_sanitize_sub_graph_name(
                f"{owner}-{repo}-issue-{number}"
            ),
        )

    async def _write_pull(
        self, owner: str, repo: str, pr: dict[str, Any]
    ) -> dict[str, Any]:
        gh = self._require_github()
        number = pr["number"]
        # Fetch one past the cap so "more exist" is knowable without
        # paginating everything.
        reviews, inline = await asyncio.gather(
            gh.list_pull_reviews(
                owner, repo, number, max_items=self._max_reviews + 1
            ),
            gh.list_pull_comments(owner, repo, number),
        )
        markdown = format_pull_request(
            pr,
            reviews[: self._max_reviews],
            inline,
            owner,
            repo,
            total_reviews=len(reviews),
        )
        return await self._dkg.memory_turn(
            context_graph_id=self._context_graph_id,
            markdown=markdown,
            layer=self._layer,
            session_uri=_repo_session_uri(owner, repo),
            sub_graph_name=_sanitize_sub_graph_name(f"{owner}-{repo}-pr-{number}"),
        )

    async def _ingest_issue(
        self,
        owner: str,
        repo: str,
        issue: dict[str, Any],
        result: IngestResult,
    ) -> None:
        async with self._sem:
            try:
                resp = await self._write_issue(owner, repo, issue)
                result.issues_ingested += 1
                if uri := resp.get("turnUri"):
                    result.turn_uris.append(uri)
            except GitHubRateLimitError:
                raise
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
                resp = await self._write_pull(owner, repo, pr)
                result.pulls_ingested += 1
                if uri := resp.get("turnUri"):
                    result.turn_uris.append(uri)
            except GitHubRateLimitError:
                raise
            except Exception as exc:
                result.errors.append(f"PR #{pr.get('number')}: {exc}")


def _repo_session_uri(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"

"""Thin async wrapper around the GitHub REST API v3."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, AsyncIterator

import httpx

_BASE = "https://api.github.com"
_PER_PAGE = 100

# Retry policy
_MAX_SECONDARY_RETRIES = 2  # secondary rate limits (Retry-After present)
_MAX_TRANSIENT_RETRIES = 1  # 5xx responses and transport/timeout errors
_TRANSIENT_BACKOFF = 1.0  # seconds
_MAX_RETRY_AFTER = 60  # cap honored Retry-After sleeps (seconds)


class GitHubRateLimitError(RuntimeError):
    """Raised when the GitHub API rate limit has been exhausted.

    Attributes:
        reset_at: Unix timestamp at which the rate limit resets.
    """

    def __init__(self, reset_at: int | None) -> None:
        self.reset_at = reset_at
        msg = "GitHub API rate limit exhausted"
        if reset_at:
            msg += f" (resets at unix={reset_at})"
        super().__init__(msg)


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO 8601 timestamp into an aware datetime, or None on failure.

    Accepts both trailing-"Z" and explicit-offset forms. Naive timestamps are
    assumed to be UTC.
    """
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class GitHubClient:
    def __init__(self, token: str | None = None, timeout: float = 30.0) -> None:
        token = token or os.environ.get("GITHUB_TOKEN", "")
        if not token:
            raise ValueError(
                "GitHub token required. Pass token= or set GITHUB_TOKEN env var."
            )
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    def _http(self) -> httpx.AsyncClient:
        """Return the shared connection-pooled client for the running loop.

        The client is cached together with the event loop it was created on;
        if the running loop changed (e.g. successive ``asyncio.run`` calls)
        a fresh client is created.
        """
        loop = asyncio.get_running_loop()
        if (
            self._client is None
            or self._client.is_closed
            or self._client_loop is not loop
        ):
            self._client = httpx.AsyncClient(
                timeout=self._timeout, headers=self._headers
            )
            self._client_loop = loop
        return self._client

    async def aclose(self) -> None:
        """Close the pooled HTTP client (if any)."""
        client, self._client, self._client_loop = self._client, None, None
        if client is not None and not client.is_closed:
            await client.aclose()

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        since: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield all issues (excluding PRs) page by page."""
        params: dict[str, Any] = {
            "state": state,
            "per_page": _PER_PAGE,
            "page": 1,
        }
        if since:
            params["since"] = since
        async for item in self._paginate(f"{_BASE}/repos/{owner}/{repo}/issues", params):
            # GitHub returns PRs in the issues endpoint; filter them out
            if "pull_request" not in item:
                yield item

    async def get_issue(
        self, owner: str, repo: str, number: int
    ) -> dict[str, Any]:
        return await self._get(f"{_BASE}/repos/{owner}/{repo}/issues/{number}")

    async def list_issue_comments(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        return await self._collect(
            f"{_BASE}/repos/{owner}/{repo}/issues/{issue_number}/comments",
            max_items=max_items,
        )

    # ------------------------------------------------------------------
    # Pull Requests
    # ------------------------------------------------------------------

    async def list_pulls(
        self,
        owner: str,
        repo: str,
        state: str = "all",
        since: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield PRs page by page.

        ``since`` is an ISO 8601 timestamp. The /pulls endpoint does not support
        a server-side ``since`` filter, so we sort by ``updated`` desc and stop
        as soon as we see a PR older than the cutoff. PRs whose ``updated_at``
        cannot be parsed are kept.
        """
        since_dt: datetime | None = None
        if since:
            since_dt = _parse_iso(since)
            if since_dt is None:
                raise ValueError(f"Invalid ISO 8601 'since' timestamp: {since!r}")
        params: dict[str, Any] = {
            "state": state,
            "per_page": _PER_PAGE,
            "page": 1,
            "sort": "updated",
            "direction": "desc",
        }
        async for pr in self._paginate(f"{_BASE}/repos/{owner}/{repo}/pulls", params):
            if since_dt is not None:
                updated_dt = _parse_iso(pr.get("updated_at") or "")
                # Unparseable updated_at → keep the PR.
                if updated_dt is not None and updated_dt < since_dt:
                    return
            yield pr

    async def get_pull(
        self, owner: str, repo: str, number: int
    ) -> dict[str, Any]:
        return await self._get(f"{_BASE}/repos/{owner}/{repo}/pulls/{number}")

    async def list_pull_reviews(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        return await self._collect(
            f"{_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
            max_items=max_items,
        )

    async def list_pull_comments(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        """Inline review comments on a PR."""
        return await self._collect(
            f"{_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/comments",
            max_items=max_items,
        )

    # ------------------------------------------------------------------
    # Repository metadata
    # ------------------------------------------------------------------

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        return await self._get(f"{_BASE}/repos/{owner}/{repo}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _request(
        self, url: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        """GET with rate-limit awareness and bounded retries.

        - Primary rate limit (403/429 with X-RateLimit-Remaining: 0) raises
          GitHubRateLimitError immediately, carrying the reset timestamp.
        - Secondary rate limits (403/429 with a Retry-After header while
          requests remain) are retried up to _MAX_SECONDARY_RETRIES times,
          sleeping min(Retry-After, 60s); then GitHubRateLimitError is raised.
        - 5xx responses and transport/timeout errors are retried once with a
          short backoff.
        """
        secondary_retries = 0
        transient_retries = 0
        while True:
            try:
                r = await self._http().get(url, params=params)
            except (httpx.TimeoutException, httpx.TransportError):
                if transient_retries >= _MAX_TRANSIENT_RETRIES:
                    raise
                transient_retries += 1
                await asyncio.sleep(_TRANSIENT_BACKOFF)
                continue

            if r.status_code in (403, 429):
                remaining = r.headers.get("X-RateLimit-Remaining")
                retry_after = r.headers.get("Retry-After")
                if remaining == "0":
                    # Primary limit exhausted — retrying is pointless.
                    reset = r.headers.get("X-RateLimit-Reset")
                    raise GitHubRateLimitError(
                        int(reset) if reset and reset.isdigit() else None
                    )
                if retry_after is not None:
                    # Secondary (abuse) limit — back off and retry.
                    if secondary_retries >= _MAX_SECONDARY_RETRIES:
                        raise GitHubRateLimitError(None)
                    secondary_retries += 1
                    try:
                        delay: float = min(int(retry_after), _MAX_RETRY_AFTER)
                    except ValueError:
                        delay = _TRANSIENT_BACKOFF
                    await asyncio.sleep(delay)
                    continue

            if r.status_code >= 500 and transient_retries < _MAX_TRANSIENT_RETRIES:
                transient_retries += 1
                await asyncio.sleep(_TRANSIENT_BACKOFF)
                continue

            r.raise_for_status()
            return r

    async def _get(self, url: str) -> dict[str, Any]:
        r = await self._request(url)
        data: dict[str, Any] = r.json()
        return data

    async def _paginate(
        self, url: str, params: dict[str, Any]
    ) -> AsyncGenerator[dict[str, Any], None]:
        while True:
            r = await self._request(url, params=params)
            page = r.json()
            if not page:
                break
            for item in page:
                yield item
            if len(page) < params["per_page"]:
                break
            params = {**params, "page": params["page"] + 1}

    async def _collect(
        self, url: str, max_items: int | None = None
    ) -> list[dict[str, Any]]:
        """Collect paginated results, stopping early once max_items is reached."""
        if max_items is not None and max_items <= 0:
            return []
        per_page = _PER_PAGE if max_items is None else min(_PER_PAGE, max_items)
        params: dict[str, Any] = {"per_page": per_page, "page": 1}
        results: list[dict[str, Any]] = []
        gen = self._paginate(url, params)
        try:
            async for item in gen:
                results.append(item)
                if max_items is not None and len(results) >= max_items:
                    break
        finally:
            await gen.aclose()
        return results

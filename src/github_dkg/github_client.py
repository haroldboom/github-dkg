"""Thin async wrapper around the GitHub REST API v3."""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

import httpx

_BASE = "https://api.github.com"
_PER_PAGE = 100


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


def _check_rate_limit(response: httpx.Response) -> None:
    """Raise GitHubRateLimitError if the response indicates rate-limit exhaustion."""
    if response.status_code in (403, 429):
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining == "0":
            reset = response.headers.get("X-RateLimit-Reset")
            raise GitHubRateLimitError(int(reset) if reset and reset.isdigit() else None)


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
        self, owner: str, repo: str, issue_number: int
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        params: dict[str, Any] = {"per_page": _PER_PAGE, "page": 1}
        async for comment in self._paginate(
            f"{_BASE}/repos/{owner}/{repo}/issues/{issue_number}/comments", params
        ):
            results.append(comment)
        return results

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
        as soon as we see a PR older than the cutoff.
        """
        params: dict[str, Any] = {
            "state": state,
            "per_page": _PER_PAGE,
            "page": 1,
            "sort": "updated",
            "direction": "desc",
        }
        async for pr in self._paginate(f"{_BASE}/repos/{owner}/{repo}/pulls", params):
            if since and (pr.get("updated_at") or "") < since:
                return
            yield pr

    async def get_pull(
        self, owner: str, repo: str, number: int
    ) -> dict[str, Any]:
        return await self._get(f"{_BASE}/repos/{owner}/{repo}/pulls/{number}")

    async def list_pull_reviews(
        self, owner: str, repo: str, pull_number: int
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        params: dict[str, Any] = {"per_page": _PER_PAGE, "page": 1}
        async for review in self._paginate(
            f"{_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/reviews", params
        ):
            results.append(review)
        return results

    async def list_pull_comments(
        self, owner: str, repo: str, pull_number: int
    ) -> list[dict[str, Any]]:
        """Inline review comments on a PR."""
        results: list[dict[str, Any]] = []
        params: dict[str, Any] = {"per_page": _PER_PAGE, "page": 1}
        async for comment in self._paginate(
            f"{_BASE}/repos/{owner}/{repo}/pulls/{pull_number}/comments", params
        ):
            results.append(comment)
        return results

    # ------------------------------------------------------------------
    # Repository metadata
    # ------------------------------------------------------------------

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        return await self._get(f"{_BASE}/repos/{owner}/{repo}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _get(self, url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            r = await http.get(url, headers=self._headers)
            _check_rate_limit(r)
            r.raise_for_status()
            return r.json()

    async def _paginate(
        self, url: str, params: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            while True:
                r = await http.get(url, headers=self._headers, params=params)
                _check_rate_limit(r)
                r.raise_for_status()
                page = r.json()
                if not page:
                    break
                for item in page:
                    yield item
                if len(page) < _PER_PAGE:
                    break
                params = {**params, "page": params["page"] + 1}

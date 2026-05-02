"""Thin async wrapper around the GitHub REST API v3."""

from __future__ import annotations

import os
from typing import Any, AsyncIterator

import httpx

_BASE = "https://api.github.com"
_PER_PAGE = 100


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
    ) -> AsyncIterator[dict[str, Any]]:
        params: dict[str, Any] = {
            "state": state,
            "per_page": _PER_PAGE,
            "page": 1,
        }
        async for pr in self._paginate(f"{_BASE}/repos/{owner}/{repo}/pulls", params):
            yield pr

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
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            r = await http.get(
                f"{_BASE}/repos/{owner}/{repo}", headers=self._headers
            )
            r.raise_for_status()
            return r.json()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _paginate(
        self, url: str, params: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            while True:
                r = await http.get(url, headers=self._headers, params=params)
                r.raise_for_status()
                page = r.json()
                if not page:
                    break
                for item in page:
                    yield item
                if len(page) < _PER_PAGE:
                    break
                params = {**params, "page": params["page"] + 1}

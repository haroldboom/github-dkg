"""Unit tests for GitHubClient with mocked HTTP."""

import pytest
import respx
import httpx

from github_dkg.github_client import GitHubClient, GitHubRateLimitError

_API = "https://api.github.com"


@pytest.fixture
def client():
    return GitHubClient(token="ghp_test")


def _issue(number, updated_at="2024-06-01T00:00:00Z"):
    return {"number": number, "updated_at": updated_at}


@pytest.mark.asyncio
@respx.mock
async def test_paginate_walks_pages(client):
    """A full first page triggers a second request; a short page stops."""
    page1 = [_issue(i) for i in range(100)]
    page2 = [_issue(100), _issue(101)]
    route = respx.get(f"{_API}/repos/o/r/issues").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json=page2),
        ]
    )
    items = [item async for item in client.list_issues("o", "r")]
    assert len(items) == 102
    assert route.call_count == 2
    assert route.calls[0].request.url.params["page"] == "1"
    assert route.calls[1].request.url.params["page"] == "2"


@pytest.mark.asyncio
@respx.mock
async def test_list_pulls_since_early_stop_mixed_formats(client):
    """since with an explicit offset stops at the first PR older than the
    cutoff even when updated_at uses the trailing-Z form."""
    prs = [
        {"number": 3, "updated_at": "2024-07-01T00:00:00Z"},
        {"number": 2, "updated_at": "2024-06-15T12:00:00+00:00"},
        {"number": 1, "updated_at": "2024-01-01T00:00:00Z"},  # older → stop
        {"number": 0, "updated_at": "2023-12-01T00:00:00Z"},
    ]
    respx.get(f"{_API}/repos/o/r/pulls").mock(
        return_value=httpx.Response(200, json=prs)
    )
    got = [
        pr["number"]
        async for pr in client.list_pulls("o", "r", since="2024-06-01T00:00:00+00:00")
    ]
    assert got == [3, 2]


@pytest.mark.asyncio
@respx.mock
async def test_list_pulls_keeps_unparseable_updated_at(client):
    prs = [
        {"number": 2, "updated_at": "not-a-date"},
        {"number": 1, "updated_at": "2024-01-01T00:00:00Z"},
    ]
    respx.get(f"{_API}/repos/o/r/pulls").mock(
        return_value=httpx.Response(200, json=prs)
    )
    got = [
        pr["number"]
        async for pr in client.list_pulls("o", "r", since="2024-06-01T00:00:00Z")
    ]
    assert got == [2]


@pytest.mark.asyncio
async def test_list_pulls_rejects_invalid_since(client):
    with pytest.raises(ValueError, match="since"):
        async for _ in client.list_pulls("o", "r", since="yesterday"):
            pass


@pytest.mark.asyncio
@respx.mock
async def test_secondary_rate_limit_retries_after_retry_after(client):
    """403 with Retry-After and remaining > 0 sleeps then retries."""
    route = respx.get(f"{_API}/repos/o/r").mock(
        side_effect=[
            httpx.Response(
                403,
                headers={"Retry-After": "0", "X-RateLimit-Remaining": "42"},
            ),
            httpx.Response(200, json={"full_name": "o/r"}),
        ]
    )
    repo = await client.get_repo("o", "r")
    assert repo["full_name"] == "o/r"
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_secondary_rate_limit_exhausts_retries(client):
    limited = httpx.Response(
        403, headers={"Retry-After": "0", "X-RateLimit-Remaining": "42"}
    )
    route = respx.get(f"{_API}/repos/o/r").mock(
        side_effect=[limited, limited, limited]
    )
    with pytest.raises(GitHubRateLimitError):
        await client.get_repo("o", "r")
    assert route.call_count == 3  # initial attempt + 2 retries


@pytest.mark.asyncio
@respx.mock
async def test_primary_rate_limit_raises_with_reset(client):
    respx.get(f"{_API}/repos/o/r").mock(
        return_value=httpx.Response(
            403,
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1234567890",
            },
        )
    )
    with pytest.raises(GitHubRateLimitError) as excinfo:
        await client.get_repo("o", "r")
    assert excinfo.value.reset_at == 1234567890


@pytest.mark.asyncio
@respx.mock
async def test_retries_once_on_5xx(client):
    route = respx.get(f"{_API}/repos/o/r").mock(
        side_effect=[
            httpx.Response(502),
            httpx.Response(200, json={"full_name": "o/r"}),
        ]
    )
    repo = await client.get_repo("o", "r")
    assert repo["full_name"] == "o/r"
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_retries_once_on_transport_error(client):
    route = respx.get(f"{_API}/repos/o/r").mock(
        side_effect=[
            httpx.ConnectError("reset"),
            httpx.Response(200, json={"full_name": "o/r"}),
        ]
    )
    repo = await client.get_repo("o", "r")
    assert repo["full_name"] == "o/r"
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_list_issue_comments_stops_at_max_items(client):
    """max_items caps both the page size and the number of collected items."""
    route = respx.get(f"{_API}/repos/o/r/issues/1/comments").mock(
        return_value=httpx.Response(200, json=[{"id": i} for i in range(21)])
    )
    comments = await client.list_issue_comments("o", "r", 1, max_items=21)
    assert len(comments) == 21
    assert route.call_count == 1
    assert route.calls[0].request.url.params["per_page"] == "21"


def test_github_client_raises_without_token():
    import os
    env_backup = os.environ.pop("GITHUB_TOKEN", None)
    try:
        with pytest.raises(ValueError, match="GitHub token required"):
            GitHubClient(token="")
    finally:
        if env_backup:
            os.environ["GITHUB_TOKEN"] = env_backup

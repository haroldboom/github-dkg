"""Unit tests for GitHubDKGIngestor with mocked HTTP."""

import asyncio

import pytest
from unittest.mock import AsyncMock, patch

from github_dkg.client import DKGClient
from github_dkg.github_client import GitHubClient, GitHubRateLimitError
from github_dkg.ingestor import GitHubDKGIngestor, _sanitize_sub_graph_name


FAKE_TURN_RESP = {
    "turnUri": "dkg://wm/turn/abc123",
    "fileHash": "sha256:deadbeef",
    "layer": "wm",
    "totalQuads": 12,
}

FAKE_ISSUE = {
    "number": 1,
    "title": "Test issue",
    "user": {"login": "alice"},
    "labels": [{"name": "bug"}],
    "state": "open",
    "state_reason": None,
    "created_at": "2024-01-01T00:00:00Z",
    "closed_at": None,
    "body": "Something is broken.",
    "html_url": "https://github.com/owner/repo/issues/1",
}

FAKE_PR = {
    "number": 2,
    "title": "Fix something",
    "user": {"login": "bob"},
    "labels": [],
    "state": "merged",
    "draft": False,
    "created_at": "2024-01-02T00:00:00Z",
    "merged_at": "2024-01-03T00:00:00Z",
    "closed_at": "2024-01-03T00:00:00Z",
    "body": "Fixes the issue.",
    "html_url": "https://github.com/owner/repo/pull/2",
    "base": {"ref": "main"},
    "head": {"ref": "fix/something"},
    "requested_reviewers": [],
}


@pytest.fixture
def dkg_client():
    return DKGClient(base_url="http://localhost:9200", token="test-token")


@pytest.fixture
def github_client():
    return GitHubClient(token="ghp_test")


@pytest.fixture
def ingestor(dkg_client, github_client):
    return GitHubDKGIngestor(
        dkg=dkg_client,
        github=github_client,
        context_graph_id="cg-test-123",
        layer="wm",
        concurrency=2,
    )


@pytest.mark.asyncio
async def test_ingest_repo_counts_issues_and_pulls(ingestor, dkg_client, github_client):
    """ingest_repo should count one issue and one PR ingested."""

    async def fake_list_issues(*args, **kwargs):
        yield FAKE_ISSUE

    async def fake_list_pulls(*args, **kwargs):
        yield FAKE_PR

    with (
        patch.object(github_client, "list_issues", fake_list_issues),
        patch.object(github_client, "list_pulls", fake_list_pulls),
        patch.object(github_client, "list_issue_comments", AsyncMock(return_value=[])),
        patch.object(github_client, "list_pull_reviews", AsyncMock(return_value=[])),
        patch.object(github_client, "list_pull_comments", AsyncMock(return_value=[])),
        patch.object(dkg_client, "memory_turn", AsyncMock(return_value=FAKE_TURN_RESP)),
    ):
        result = await ingestor.ingest_repo("owner", "repo")

    assert result.issues_ingested == 1
    assert result.pulls_ingested == 1
    assert result.total == 2
    assert result.errors == []
    assert "dkg://wm/turn/abc123" in result.turn_uris


@pytest.mark.asyncio
async def test_ingest_repo_skips_pulls_when_disabled(ingestor, dkg_client, github_client):
    async def fake_list_issues(*args, **kwargs):
        yield FAKE_ISSUE

    with (
        patch.object(github_client, "list_issues", fake_list_issues),
        patch.object(github_client, "list_issue_comments", AsyncMock(return_value=[])),
        patch.object(dkg_client, "memory_turn", AsyncMock(return_value=FAKE_TURN_RESP)),
    ):
        result = await ingestor.ingest_repo("owner", "repo", include_pulls=False)

    assert result.issues_ingested == 1
    assert result.pulls_ingested == 0


@pytest.mark.asyncio
async def test_ingest_repo_records_errors(ingestor, dkg_client, github_client):
    async def fake_list_issues(*args, **kwargs):
        yield FAKE_ISSUE

    with (
        patch.object(github_client, "list_issues", fake_list_issues),
        patch.object(github_client, "list_issue_comments", AsyncMock(return_value=[])),
        patch.object(github_client, "list_pulls", AsyncMock(return_value=iter([]))),
        patch.object(
            dkg_client,
            "memory_turn",
            AsyncMock(side_effect=Exception("DKG node down")),
        ),
    ):
        result = await ingestor.ingest_repo("owner", "repo", include_pulls=False)

    assert result.issues_ingested == 0
    assert len(result.errors) == 1
    assert "DKG node down" in result.errors[0]


@pytest.mark.asyncio
async def test_promote_calls_assertion_promote(ingestor, dkg_client):
    turn_uri = "dkg://wm/turn/myassertion"
    mock_resp = {"promoted": True, "sharedUri": "dkg://swm/myassertion"}

    with patch.object(
        dkg_client, "assertion_promote", AsyncMock(return_value=mock_resp)
    ) as mock_promote:
        resp = await ingestor.promote(turn_uri)

    mock_promote.assert_called_once_with(
        name="myassertion",
        context_graph_id="cg-test-123",
    )
    assert resp == mock_resp


@pytest.mark.asyncio
async def test_promote_uses_whole_uri_when_no_slash(ingestor, dkg_client):
    with patch.object(
        dkg_client, "assertion_promote", AsyncMock(return_value={})
    ) as mock_promote:
        await ingestor.promote("bare-assertion-name")

    mock_promote.assert_called_once_with(
        name="bare-assertion-name",
        context_graph_id="cg-test-123",
    )


def test_sanitize_sub_graph_name():
    assert _sanitize_sub_graph_name("owner-repo-issue-1") == "owner-repo-issue-1"
    assert _sanitize_sub_graph_name("own/er-re/po-pr-2") == "own-er-re-po-pr-2"
    assert _sanitize_sub_graph_name("a b:c/d") == "a-b-c-d"


@pytest.mark.asyncio
async def test_ingest_issue_passes_sanitized_sub_graph_name(
    ingestor, dkg_client, github_client
):
    turn_mock = AsyncMock(return_value=FAKE_TURN_RESP)
    with (
        patch.object(github_client, "get_issue", AsyncMock(return_value=FAKE_ISSUE)),
        patch.object(
            github_client, "list_issue_comments", AsyncMock(return_value=[])
        ),
        patch.object(dkg_client, "memory_turn", turn_mock),
    ):
        await ingestor.ingest_issue("own/er", "repo", 1)

    kwargs = turn_mock.call_args.kwargs
    assert kwargs["sub_graph_name"] == "own-er-repo-issue-1"
    assert "/" not in kwargs["sub_graph_name"]


@pytest.mark.asyncio
async def test_ingest_pull_passes_sub_graph_name(ingestor, dkg_client, github_client):
    turn_mock = AsyncMock(return_value=FAKE_TURN_RESP)
    with (
        patch.object(github_client, "get_pull", AsyncMock(return_value=FAKE_PR)),
        patch.object(github_client, "list_pull_reviews", AsyncMock(return_value=[])),
        patch.object(github_client, "list_pull_comments", AsyncMock(return_value=[])),
        patch.object(dkg_client, "memory_turn", turn_mock),
    ):
        await ingestor.ingest_pull("owner", "repo", 2)

    assert turn_mock.call_args.kwargs["sub_graph_name"] == "owner-repo-pr-2"


@pytest.mark.asyncio
async def test_rate_limit_reraises_and_cancels_remaining(
    ingestor, dkg_client, github_client
):
    """GitHubRateLimitError is not swallowed into result.errors; in-flight
    work is cancelled and the partial result is attached to the exception."""
    cancelled: list[int] = []

    async def fake_list_issues(*args, **kwargs):
        yield {**FAKE_ISSUE, "number": 1}
        yield {**FAKE_ISSUE, "number": 2}

    async def fake_list_comments(owner, repo, number, **kwargs):
        if number == 1:
            # Let the second task start before blowing up.
            await asyncio.sleep(0.01)
            raise GitHubRateLimitError(1234567890)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(number)
            raise
        return []

    with (
        patch.object(github_client, "list_issues", fake_list_issues),
        patch.object(github_client, "list_issue_comments", fake_list_comments),
        patch.object(dkg_client, "memory_turn", AsyncMock(return_value=FAKE_TURN_RESP)),
    ):
        with pytest.raises(GitHubRateLimitError) as excinfo:
            await ingestor.ingest_repo("owner", "repo", include_pulls=False)

    assert cancelled == [2]
    partial = excinfo.value.partial_result
    assert partial.issues_ingested == 0
    assert partial.errors == []


@pytest.mark.asyncio
async def test_listing_error_attaches_partial_result(
    ingestor, dkg_client, github_client
):
    """Errors from the listing itself propagate, carrying results so far."""

    async def failing_list_issues(*args, **kwargs):
        yield FAKE_ISSUE
        raise RuntimeError("listing exploded")

    with (
        patch.object(github_client, "list_issues", failing_list_issues),
        patch.object(
            github_client, "list_issue_comments", AsyncMock(return_value=[])
        ),
        patch.object(dkg_client, "memory_turn", AsyncMock(return_value=FAKE_TURN_RESP)),
    ):
        with pytest.raises(RuntimeError, match="listing exploded") as excinfo:
            await ingestor.ingest_repo("owner", "repo", include_pulls=False)

    partial = excinfo.value.partial_result
    assert partial.issues_ingested + len(partial.errors) <= 1

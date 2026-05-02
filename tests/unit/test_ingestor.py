"""Unit tests for GitHubDKGIngestor with mocked HTTP."""

import pytest
import respx
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from github_dkg.client import DKGClient
from github_dkg.github_client import GitHubClient
from github_dkg.ingestor import GitHubDKGIngestor


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

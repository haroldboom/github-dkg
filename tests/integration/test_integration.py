"""Integration tests against a live DKG v10 node.

Run with:
    DKG_TOKEN=<token> DKG_BASE_URL=http://localhost:9200 \\
    GITHUB_TOKEN=<token> DKG_CONTEXT_GRAPH=<id> \\
    pytest tests/integration/ -v

These are skipped automatically when the required env vars are absent.
"""

import os
import pytest
from github_dkg.client import DKGClient
from github_dkg.github_client import GitHubClient
from github_dkg.ingestor import GitHubDKGIngestor

_REQUIRED = ("DKG_TOKEN", "DKG_BASE_URL", "GITHUB_TOKEN", "DKG_CONTEXT_GRAPH")
_missing = [v for v in _REQUIRED if not os.environ.get(v)]
pytestmark = pytest.mark.skipif(
    bool(_missing),
    reason=f"Integration env vars not set: {', '.join(_missing)}",
)


@pytest.fixture
def dkg():
    return DKGClient()


@pytest.fixture
def gh():
    return GitHubClient()


@pytest.fixture
def context_graph_id():
    return os.environ["DKG_CONTEXT_GRAPH"]


@pytest.mark.asyncio
async def test_dkg_node_reachable(dkg):
    assert await dkg.ping(), "DKG node not reachable — check DKG_BASE_URL and DKG_TOKEN"


@pytest.mark.asyncio
async def test_ingest_single_public_issue(dkg, gh, context_graph_id):
    """Ingest a known public issue from the DKG repo itself."""
    ingestor = GitHubDKGIngestor(dkg=dkg, github=gh, context_graph_id=context_graph_id)
    # OriginTrail/dkg-node issue #1 is a stable public reference
    resp = await ingestor.ingest_issue("OriginTrail", "dkg-node", 1)
    assert "turnUri" in resp, f"Unexpected response: {resp}"
    assert resp["turnUri"]


@pytest.mark.asyncio
async def test_search_after_ingest(dkg, gh, context_graph_id):
    """Ingest then immediately search — result count should be >= 1."""
    ingestor = GitHubDKGIngestor(dkg=dkg, github=gh, context_graph_id=context_graph_id)
    await ingestor.ingest_issue("OriginTrail", "dkg-node", 1)
    result = await dkg.memory_search(
        context_graph_id, "DKG", limit=5, memory_layers=["wm", "swm"]
    )
    assert result.get("resultCount", 0) >= 1

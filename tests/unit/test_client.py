"""Unit tests for DKGClient with mocked HTTP."""

import pytest
import respx
import httpx

from github_dkg.client import DKGClient


@pytest.fixture
def client():
    return DKGClient(base_url="http://localhost:9200", token="test-token")


@pytest.mark.asyncio
@respx.mock
async def test_ping_returns_true_on_200(client):
    respx.get("http://localhost:9200/api/agents").mock(return_value=httpx.Response(200))
    assert await client.ping() is True


@pytest.mark.asyncio
@respx.mock
async def test_ping_returns_false_on_401(client):
    respx.get("http://localhost:9200/api/agents").mock(return_value=httpx.Response(401))
    assert await client.ping() is False


@pytest.mark.asyncio
@respx.mock
async def test_ping_returns_false_on_connection_error(client):
    respx.get("http://localhost:9200/api/agents").mock(side_effect=httpx.ConnectError("refused"))
    assert await client.ping() is False


@pytest.mark.asyncio
@respx.mock
async def test_memory_turn_posts_correct_body(client):
    respx.post("http://localhost:9200/api/memory/turn").mock(
        return_value=httpx.Response(200, json={"turnUri": "dkg://wm/abc", "totalQuads": 8})
    )
    resp = await client.memory_turn(
        context_graph_id="cg-123",
        markdown="**Human:** hello",
        layer="wm",
    )
    assert resp["turnUri"] == "dkg://wm/abc"
    req = respx.calls.last.request
    import json
    body = json.loads(req.content)
    assert body["contextGraphId"] == "cg-123"
    assert body["layer"] == "wm"
    assert "**Human:** hello" in body["markdown"]


@pytest.mark.asyncio
@respx.mock
async def test_memory_turn_includes_session_uri(client):
    respx.post("http://localhost:9200/api/memory/turn").mock(
        return_value=httpx.Response(200, json={"turnUri": "dkg://wm/abc"})
    )
    await client.memory_turn(
        context_graph_id="cg-123",
        markdown="content",
        session_uri="https://github.com/owner/repo",
    )
    req = respx.calls.last.request
    import json
    body = json.loads(req.content)
    assert body["sessionUri"] == "https://github.com/owner/repo"


@pytest.mark.asyncio
@respx.mock
async def test_memory_search(client):
    respx.post("http://localhost:9200/api/memory/search").mock(
        return_value=httpx.Response(200, json={"resultCount": 2, "results": []})
    )
    resp = await client.memory_search("cg-123", "auth bug", limit=5)
    assert resp["resultCount"] == 2
    req = respx.calls.last.request
    import json
    body = json.loads(req.content)
    assert body["query"] == "auth bug"
    assert body["limit"] == 5


@pytest.mark.asyncio
@respx.mock
async def test_assertion_promote(client):
    respx.post("http://localhost:9200/api/assertion/myname/promote").mock(
        return_value=httpx.Response(200, json={"promoted": True})
    )
    resp = await client.assertion_promote("myname", "cg-123")
    assert resp["promoted"] is True


@pytest.mark.asyncio
@respx.mock
async def test_memory_turn_raises_on_error(client):
    respx.post("http://localhost:9200/api/memory/turn").mock(
        return_value=httpx.Response(500, json={"error": "internal"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.memory_turn("cg-123", "content")


def test_client_raises_without_token():
    import os
    env_backup = os.environ.pop("DKG_TOKEN", None)
    try:
        with pytest.raises(ValueError, match="DKG bearer token required"):
            DKGClient(base_url="http://localhost:9200", token="")
    finally:
        if env_backup:
            os.environ["DKG_TOKEN"] = env_backup

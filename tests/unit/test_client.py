"""Unit tests for DKGClient with mocked HTTP."""

import json

import pytest
import respx
import httpx

from github_dkg.client import (
    DKGClient,
    DKGPromoteError,
    DKGPublishError,
    coerce_trust_level,
)


@pytest.fixture
def client():
    return DKGClient(base_url="http://localhost:9200", token="test-token")


@pytest.mark.asyncio
@respx.mock
async def test_ping_returns_true_on_200(client):
    respx.get("http://localhost:9200/api/context-graph/list").mock(
        return_value=httpx.Response(200, json=[])
    )
    assert await client.ping() is True


@pytest.mark.asyncio
@respx.mock
async def test_ping_raises_on_401(client):
    """HTTP error statuses raise so a bad token is distinguishable from an
    unreachable node."""
    respx.get("http://localhost:9200/api/context-graph/list").mock(
        return_value=httpx.Response(401)
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.ping()


@pytest.mark.asyncio
@respx.mock
async def test_ping_returns_false_on_connection_error(client):
    respx.get("http://localhost:9200/api/context-graph/list").mock(
        side_effect=httpx.ConnectError("refused")
    )
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
    body = json.loads(req.content)
    assert body["sessionUri"] == "https://github.com/owner/repo"


@pytest.mark.asyncio
@respx.mock
async def test_memory_turn_includes_sub_graph_name(client):
    respx.post("http://localhost:9200/api/memory/turn").mock(
        return_value=httpx.Response(200, json={"turnUri": "dkg://wm/abc"})
    )
    await client.memory_turn(
        context_graph_id="cg-123",
        markdown="content",
        sub_graph_name="owner-repo-issue-1",
    )
    body = json.loads(respx.calls.last.request.content)
    assert body["subGraphName"] == "owner-repo-issue-1"


@pytest.mark.asyncio
@respx.mock
async def test_memory_search_defaults_layers_when_none(client):
    """None → the wm,swm default is sent (omitting memoryLayers returns 0
    results on current node builds)."""
    respx.post("http://localhost:9200/api/memory/search").mock(
        return_value=httpx.Response(200, json={"resultCount": 2, "results": []})
    )
    resp = await client.memory_search("cg-123", "auth bug", limit=5)
    assert resp["resultCount"] == 2
    body = json.loads(respx.calls.last.request.content)
    assert body["query"] == "auth bug"
    assert body["limit"] == 5
    assert body["memoryLayers"] == ["wm", "swm"]


@pytest.mark.asyncio
@respx.mock
async def test_memory_search_sends_explicit_layers(client):
    respx.post("http://localhost:9200/api/memory/search").mock(
        return_value=httpx.Response(200, json={"resultCount": 0, "results": []})
    )
    await client.memory_search("cg-123", "q", memory_layers=["swm"])
    body = json.loads(respx.calls.last.request.content)
    assert body["memoryLayers"] == ["swm"]


@pytest.mark.asyncio
@respx.mock
async def test_memory_search_sends_explicit_empty_layers(client):
    """An explicit empty list is sent verbatim, not replaced by the default."""
    respx.post("http://localhost:9200/api/memory/search").mock(
        return_value=httpx.Response(200, json={"resultCount": 0, "results": []})
    )
    await client.memory_search("cg-123", "q", memory_layers=[])
    body = json.loads(respx.calls.last.request.content)
    assert body["memoryLayers"] == []


@pytest.mark.asyncio
@respx.mock
async def test_create_context_graph_sends_id_and_name(client):
    respx.post("http://localhost:9200/api/context-graph/create").mock(
        return_value=httpx.Response(200, json={"created": "my-graph", "uri": "dkg://cg/my-graph"})
    )
    resp = await client.create_context_graph("my-graph")
    assert resp["created"] == "my-graph"
    body = json.loads(respx.calls.last.request.content)
    assert body == {"id": "my-graph", "name": "my-graph"}


@pytest.mark.asyncio
@respx.mock
async def test_create_context_graph_explicit_id(client):
    respx.post("http://localhost:9200/api/context-graph/create").mock(
        return_value=httpx.Response(200, json={"created": "cg-42", "uri": "dkg://cg/cg-42"})
    )
    await client.create_context_graph("Pretty Name", id="cg-42")
    body = json.loads(respx.calls.last.request.content)
    assert body == {"id": "cg-42", "name": "Pretty Name"}


@pytest.mark.asyncio
@respx.mock
async def test_assertion_promote_happy_path(client):
    """Submit → poll (running → succeeded) → final job view returned."""
    submit = respx.post("http://localhost:9200/api/knowledge-assets/myname/swm/share-async").mock(
        return_value=httpx.Response(200, json={"jobId": "job-1", "state": "queued"})
    )
    poll = respx.get("http://localhost:9200/api/knowledge-assets/swm/share-jobs/job-1").mock(
        side_effect=[
            httpx.Response(200, json={"jobId": "job-1", "state": "running"}),
            httpx.Response(200, json={"jobId": "job-1", "state": "succeeded"}),
        ]
    )
    job = await client.assertion_promote(
        "myname", "cg-123", poll_interval=0.01, poll_timeout=5.0
    )
    assert job["state"] == "succeeded"
    assert submit.call_count == 1
    assert poll.call_count == 2
    body = json.loads(submit.calls.last.request.content)
    assert body == {"contextGraphId": "cg-123"}


@pytest.mark.asyncio
@respx.mock
async def test_assertion_promote_quotes_name(client):
    respx.post("http://localhost:9200/api/knowledge-assets/my%3Aname/swm/share-async").mock(
        return_value=httpx.Response(200, json={"jobId": "job-2", "state": "queued"})
    )
    respx.get("http://localhost:9200/api/knowledge-assets/swm/share-jobs/job-2").mock(
        return_value=httpx.Response(200, json={"jobId": "job-2", "state": "succeeded"})
    )
    job = await client.assertion_promote("my:name", "cg-123", poll_interval=0.01)
    assert job["state"] == "succeeded"


@pytest.mark.asyncio
@respx.mock
async def test_assertion_promote_failed_job_raises(client):
    respx.post("http://localhost:9200/api/knowledge-assets/myname/swm/share-async").mock(
        return_value=httpx.Response(200, json={"jobId": "job-3", "state": "queued"})
    )
    respx.get("http://localhost:9200/api/knowledge-assets/swm/share-jobs/job-3").mock(
        return_value=httpx.Response(
            200, json={"jobId": "job-3", "state": "failed", "error": "boom"}
        )
    )
    with pytest.raises(DKGPromoteError, match="failed"):
        await client.assertion_promote("myname", "cg-123", poll_interval=0.01)


@pytest.mark.asyncio
@respx.mock
async def test_assertion_promote_timeout_raises(client):
    respx.post("http://localhost:9200/api/knowledge-assets/myname/swm/share-async").mock(
        return_value=httpx.Response(200, json={"jobId": "job-4", "state": "queued"})
    )
    respx.get("http://localhost:9200/api/knowledge-assets/swm/share-jobs/job-4").mock(
        return_value=httpx.Response(200, json={"jobId": "job-4", "state": "running"})
    )
    with pytest.raises(DKGPromoteError, match="did not finish"):
        await client.assertion_promote(
            "myname", "cg-123", poll_interval=0.01, poll_timeout=0.05
        )


@pytest.mark.asyncio
@respx.mock
async def test_assertion_promote_conflict_polls_existing_job(client):
    respx.post("http://localhost:9200/api/knowledge-assets/myname/swm/share-async").mock(
        return_value=httpx.Response(409, json={"existingJobId": "job-5"})
    )
    respx.get("http://localhost:9200/api/knowledge-assets/swm/share-jobs/job-5").mock(
        return_value=httpx.Response(200, json={"jobId": "job-5", "state": "succeeded"})
    )
    job = await client.assertion_promote("myname", "cg-123", poll_interval=0.01)
    assert job["jobId"] == "job-5"


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


# ----------------------------------------------------------------------
# Verifiable Memory (trust gradient)
# ----------------------------------------------------------------------

_SEAL = {
    "assertionUri": "dkg://wm/ka/d1",
    "merkleRoot": "0xroot",
    "authorAddress": "0xauthor",
    "chainId": 2043,
    "kav10Address": "0xkav10",
    "eip712Digest": "0xdigest",
    "schemeVersion": 1,
}


@pytest.mark.asyncio
@respx.mock
async def test_ka_create_sends_name_and_context_graph(client):
    respx.post("http://localhost:9200/api/knowledge-assets").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "d1",
                "assertionUri": "dkg://wm/ka/d1",
                "alreadyExists": False,
                "status": "draft-open",
            },
        )
    )
    resp = await client.ka_create("d1", context_graph_id="cg-1")
    assert resp["status"] == "draft-open"
    assert resp["alreadyExists"] is False
    body = json.loads(respx.calls.last.request.content)
    assert body == {"contextGraphId": "cg-1", "name": "d1"}


@pytest.mark.asyncio
@respx.mock
async def test_ka_create_includes_sub_graph_name(client):
    respx.post("http://localhost:9200/api/knowledge-assets").mock(
        return_value=httpx.Response(200, json={"name": "d1", "status": "draft-open"})
    )
    await client.ka_create("d1", context_graph_id="cg-1", sub_graph_name="grp")
    body = json.loads(respx.calls.last.request.content)
    assert body["subGraphName"] == "grp"


@pytest.mark.asyncio
@respx.mock
async def test_ka_write_sends_quads(client):
    respx.post("http://localhost:9200/api/knowledge-assets/d1/wm/write").mock(
        return_value=httpx.Response(200, json={"written": 2})
    )
    quads = [
        {"subject": "urn:s", "predicate": "urn:p", "object": "o1"},
        {"subject": "urn:s", "predicate": "urn:p2", "object": "o2"},
    ]
    resp = await client.ka_write("d1", context_graph_id="cg-1", quads=quads)
    assert resp["written"] == 2
    body = json.loads(respx.calls.last.request.content)
    assert body == {"contextGraphId": "cg-1", "quads": quads}


@pytest.mark.asyncio
@respx.mock
async def test_ka_finalize_returns_seal(client):
    respx.post("http://localhost:9200/api/knowledge-assets/d1/wm/finalize").mock(
        return_value=httpx.Response(200, json=_SEAL)
    )
    resp = await client.ka_finalize("d1", context_graph_id="cg-1")
    assert resp["merkleRoot"] == "0xroot"
    assert resp["eip712Digest"] == "0xdigest"
    body = json.loads(respx.calls.last.request.content)
    assert body == {"contextGraphId": "cg-1"}


@pytest.mark.asyncio
@respx.mock
async def test_vm_publish_success(client):
    respx.post("http://localhost:9200/api/knowledge-assets/d1/vm/publish").mock(
        return_value=httpx.Response(
            200,
            json={
                "kaId": "ka-9",
                "status": "confirmed",
                "ual": "did:dkg:otp:2043/0xabc/9",
                "txHash": "0xtx",
            },
        )
    )
    resp = await client.vm_publish("d1", context_graph_id="cg-1", publish_epochs=3)
    assert resp["status"] == "confirmed"
    assert resp["ual"] == "did:dkg:otp:2043/0xabc/9"
    body = json.loads(respx.calls.last.request.content)
    assert body == {"contextGraphId": "cg-1", "publishEpochs": 3}


@pytest.mark.asyncio
@respx.mock
async def test_vm_publish_omits_epochs_when_none(client):
    respx.post("http://localhost:9200/api/knowledge-assets/d1/vm/publish").mock(
        return_value=httpx.Response(200, json={"status": "confirmed"})
    )
    await client.vm_publish("d1", context_graph_id="cg-1")
    body = json.loads(respx.calls.last.request.content)
    assert "publishEpochs" not in body


@pytest.mark.asyncio
@respx.mock
async def test_vm_publish_207_returns_partial_body(client):
    """207 = minted on-chain but Context Graph binding failed — the mint
    succeeded, so the body is returned rather than raised."""
    respx.post("http://localhost:9200/api/knowledge-assets/d1/vm/publish").mock(
        return_value=httpx.Response(
            207, json={"kaId": "ka-9", "status": "confirmed", "ual": "did:dkg:x"}
        )
    )
    resp = await client.vm_publish("d1", context_graph_id="cg-1")
    assert resp["kaId"] == "ka-9"


@pytest.mark.asyncio
@respx.mock
async def test_vm_publish_409_precondition_raises_with_body(client):
    err_body = {"code": "VM_PUBLISH_PRECONDITION", "message": "not sealed"}
    respx.post("http://localhost:9200/api/knowledge-assets/d1/vm/publish").mock(
        return_value=httpx.Response(409, json=err_body)
    )
    with pytest.raises(DKGPublishError, match="VM_PUBLISH_PRECONDITION") as excinfo:
        await client.vm_publish("d1", context_graph_id="cg-1")
    assert excinfo.value.body == err_body
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
@respx.mock
async def test_vm_publish_400_raises_with_body(client):
    respx.post("http://localhost:9200/api/knowledge-assets/d1/vm/publish").mock(
        return_value=httpx.Response(400, json={"error": "bad request"})
    )
    with pytest.raises(DKGPublishError) as excinfo:
        await client.vm_publish("d1", context_graph_id="cg-1")
    assert excinfo.value.status_code == 400
    assert excinfo.value.body == {"error": "bad request"}


@pytest.mark.asyncio
@respx.mock
async def test_vm_publish_502_non_json_body(client):
    respx.post("http://localhost:9200/api/knowledge-assets/d1/vm/publish").mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )
    with pytest.raises(DKGPublishError, match="502") as excinfo:
        await client.vm_publish("d1", context_graph_id="cg-1")
    assert excinfo.value.body is None


@pytest.mark.asyncio
@respx.mock
async def test_endorse_sends_ual(client):
    respx.post("http://localhost:9200/api/endorse").mock(
        return_value=httpx.Response(200, json={"queued": True})
    )
    resp = await client.endorse(context_graph_id="cg-1", ual="did:dkg:otp/0xabc/9")
    assert resp == {"queued": True}
    body = json.loads(respx.calls.last.request.content)
    assert body == {"contextGraphId": "cg-1", "ual": "did:dkg:otp/0xabc/9"}


@pytest.mark.asyncio
@respx.mock
async def test_request_verification_verified(client):
    respx.post("http://localhost:9200/api/verify").mock(
        return_value=httpx.Response(200, json={"status": "verified"})
    )
    resp = await client.request_verification(
        "cg-1", "vm-1", "batch-1", required_signatures=2
    )
    assert resp["status"] == "verified"
    body = json.loads(respx.calls.last.request.content)
    assert body == {
        "contextGraphId": "cg-1",
        "verifiableMemoryId": "vm-1",
        "batchId": "batch-1",
        "requiredSignatures": 2,
    }


@pytest.mark.asyncio
@respx.mock
async def test_request_verification_409_returns_body(client):
    """A quorum shortfall (partial / no_quorum) is a result, not an error."""
    respx.post("http://localhost:9200/api/verify").mock(
        return_value=httpx.Response(409, json={"status": "no_quorum", "signatures": []})
    )
    resp = await client.request_verification("cg-1", "vm-1", "batch-1")
    assert resp["status"] == "no_quorum"
    body = json.loads(respx.calls.last.request.content)
    assert "requiredSignatures" not in body


@pytest.mark.asyncio
@respx.mock
async def test_request_verification_500_raises(client):
    respx.post("http://localhost:9200/api/verify").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.request_verification("cg-1", "vm-1", "batch-1")


@pytest.mark.asyncio
@respx.mock
async def test_kc_metadata(client):
    respx.get("http://localhost:9200/api/kc/ka-9").mock(
        return_value=httpx.Response(
            200, json={"merkleRoot": "0xroot", "author": "0xauthor"}
        )
    )
    resp = await client.kc_metadata("ka-9")
    assert resp == {"merkleRoot": "0xroot", "author": "0xauthor"}


@pytest.mark.asyncio
@respx.mock
async def test_kc_metadata_author(client):
    respx.get("http://localhost:9200/api/kc/ka-9/author").mock(
        return_value=httpx.Response(200, json={"author": "0xauthor", "attested": True})
    )
    resp = await client.kc_metadata("ka-9", author=True)
    assert resp["attested"] is True


@pytest.mark.asyncio
@respx.mock
async def test_query_sends_trust_gradient_fields(client):
    respx.post("http://localhost:9200/api/query").mock(
        return_value=httpx.Response(200, json={"results": {"bindings": []}})
    )
    await client.query(
        "SELECT * WHERE { ?s ?p ?o }",
        context_graph_id="cg-1",
        view="verifiable-memory",
        min_trust="endorsed",
    )
    body = json.loads(respx.calls.last.request.content)
    assert body["contextGraphId"] == "cg-1"
    assert body["view"] == "verifiable-memory"
    assert body["minTrust"] == 1  # names are normalized to ints on the wire


@pytest.mark.asyncio
@respx.mock
async def test_query_omits_trust_fields_when_unset(client):
    respx.post("http://localhost:9200/api/query").mock(
        return_value=httpx.Response(200, json={"results": {"bindings": []}})
    )
    await client.query("SELECT * WHERE { ?s ?p ?o }")
    body = json.loads(respx.calls.last.request.content)
    assert body == {
        "sparql": "SELECT * WHERE { ?s ?p ?o }",
        "includeWorkspace": True,
    }


def test_coerce_trust_level():
    assert coerce_trust_level(0) == 0
    assert coerce_trust_level("3") == 3
    assert coerce_trust_level("selfAttested") == 0
    assert coerce_trust_level("endorsed") == 1
    assert coerce_trust_level("partially-verified") == 2
    assert coerce_trust_level("CONSENSUS_VERIFIED") == 3
    with pytest.raises(ValueError, match="Unknown trust level"):
        coerce_trust_level("mostlyTrusted")
    with pytest.raises(ValueError, match="between 0 and 3"):
        coerce_trust_level(4)


@respx.mock
@pytest.mark.asyncio
async def test_assertion_promote_falls_back_to_legacy_routes(client):
    """Older node builds 404 the knowledge-assets surface — fall back to the
    legacy /api/assertion promote-async routes."""
    respx.post("http://localhost:9200/api/knowledge-assets/a1/swm/share-async").mock(
        return_value=httpx.Response(404, json={"error": "Not found"})
    )
    respx.post("http://localhost:9200/api/assertion/a1/promote-async").mock(
        return_value=httpx.Response(200, json={"jobId": "job-l", "state": "queued"})
    )
    respx.get("http://localhost:9200/api/assertion/promote-async/job-l").mock(
        return_value=httpx.Response(200, json={"jobId": "job-l", "state": "succeeded"})
    )
    job = await client.assertion_promote("a1", context_graph_id="cg")
    assert job["state"] == "succeeded"
    await client.aclose()

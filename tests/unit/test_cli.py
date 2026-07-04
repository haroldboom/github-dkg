"""Unit tests for the CLI using click's CliRunner and mocked HTTP."""

import json

import httpx
import respx
from click.testing import CliRunner

from github_dkg.cli import main

_DKG = "http://localhost:9200"
_COMMON = ["--dkg-token", "test-token", "--dkg-url", _DKG]


@respx.mock
def test_search_sends_layers_in_request_body():
    route = respx.post(f"{_DKG}/api/memory/search").mock(
        return_value=httpx.Response(200, json={"resultCount": 0, "results": []})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["search", "auth bug", "--context-graph", "cg-1", "--layers", "wm", *_COMMON],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert body["memoryLayers"] == ["wm"]
    assert body["query"] == "auth bug"


@respx.mock
def test_search_default_layers():
    route = respx.post(f"{_DKG}/api/memory/search").mock(
        return_value=httpx.Response(200, json={"resultCount": 0, "results": []})
    )
    runner = CliRunner()
    result = runner.invoke(
        main, ["search", "q", "--context-graph", "cg-1", *_COMMON]
    )
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert body["memoryLayers"] == ["wm", "swm"]


@respx.mock
def test_create_context_graph_command():
    route = respx.post(f"{_DKG}/api/context-graph/create").mock(
        return_value=httpx.Response(
            200, json={"created": "cg-42", "uri": "dkg://cg/cg-42"}
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["create-context-graph", "My Graph", "--id", "cg-42", *_COMMON],
    )
    assert result.exit_code == 0, result.output
    assert "cg-42" in result.output
    body = json.loads(route.calls.last.request.content)
    assert body == {"id": "cg-42", "name": "My Graph"}


@respx.mock
def test_create_context_graph_defaults_id_to_name():
    route = respx.post(f"{_DKG}/api/context-graph/create").mock(
        return_value=httpx.Response(
            200, json={"created": "my-graph", "uri": "dkg://cg/my-graph"}
        )
    )
    runner = CliRunner()
    result = runner.invoke(main, ["create-context-graph", "my-graph", *_COMMON])
    assert result.exit_code == 0, result.output
    body = json.loads(route.calls.last.request.content)
    assert body == {"id": "my-graph", "name": "my-graph"}


def test_ingest_rejects_zero_concurrency():
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "ingest",
            "owner/repo",
            "--context-graph",
            "cg-1",
            "--concurrency",
            "0",
            "--github-token",
            "ghp_x",
            *_COMMON,
        ],
    )
    assert result.exit_code == 2
    assert "concurrency" in result.output


@respx.mock
def test_ingest_normalizes_bare_since_date():
    """A bare YYYY-MM-DD --since is expanded to T00:00:00Z before use."""
    respx.get(f"{_DKG}/api/context-graph/list").mock(
        return_value=httpx.Response(200, json=[])
    )
    issues = respx.get("https://api.github.com/repos/owner/repo/issues").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get("https://api.github.com/repos/owner/repo/pulls").mock(
        return_value=httpx.Response(200, json=[])
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "ingest",
            "owner/repo",
            "--context-graph",
            "cg-1",
            "--since",
            "2024-06-01",
            "--github-token",
            "ghp_x",
            *_COMMON,
        ],
    )
    assert result.exit_code == 0, result.output
    assert issues.calls.last.request.url.params["since"] == "2024-06-01T00:00:00Z"


_KA = f"{_DKG}/api/knowledge-assets/owner-repo-pr-2-decision"

_FAKE_PR = {
    "number": 2,
    "title": "Adopt monorepo",
    "user": {"login": "alice"},
    "created_at": "2024-01-02T00:00:00Z",
    "merged_at": "2024-01-05T00:00:00Z",
    "closed_at": "2024-01-05T00:00:00Z",
    "body": "Decision rationale.",
    "html_url": "https://github.com/owner/repo/pull/2",
}


def _mock_publish_chain(publish_response: httpx.Response) -> respx.Route:
    respx.get("https://api.github.com/repos/owner/repo/pulls/2").mock(
        return_value=httpx.Response(200, json=_FAKE_PR)
    )
    respx.post(f"{_DKG}/api/knowledge-assets").mock(
        return_value=httpx.Response(200, json={"status": "draft-open"})
    )
    respx.post(f"{_KA}/wm/write").mock(
        return_value=httpx.Response(200, json={"written": 8})
    )
    respx.post(f"{_KA}/wm/finalize").mock(
        return_value=httpx.Response(200, json={"merkleRoot": "0xroot"})
    )
    respx.post(f"{_KA}/swm/share-async").mock(
        return_value=httpx.Response(200, json={"jobId": "j1", "state": "queued"})
    )
    respx.get(f"{_DKG}/api/knowledge-assets/swm/share-jobs/j1").mock(
        return_value=httpx.Response(200, json={"jobId": "j1", "state": "succeeded"})
    )
    return respx.post(f"{_KA}/vm/publish").mock(return_value=publish_response)


@respx.mock
def test_publish_decision_command():
    publish = _mock_publish_chain(
        httpx.Response(
            200,
            json={
                "kaId": "ka-9",
                "status": "confirmed",
                "ual": "did:dkg:otp:2043/0xabc/9",
                "txHash": "0xtx",
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "publish-decision",
            "owner/repo",
            "2",
            "--type",
            "pr",
            "--context-graph",
            "cg-1",
            "--epochs",
            "3",
            "--github-token",
            "ghp_x",
            *_COMMON,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "did:dkg:otp:2043/0xabc/9" in result.output
    assert "0xtx" in result.output
    assert "0xroot" in result.output
    body = json.loads(publish.calls.last.request.content)
    assert body == {"contextGraphId": "cg-1", "publishEpochs": 3}


@respx.mock
def test_publish_decision_command_reports_publish_rejection():
    _mock_publish_chain(
        httpx.Response(409, json={"code": "VM_PUBLISH_PRECONDITION"})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "publish-decision",
            "owner/repo",
            "2",
            "--type",
            "pr",
            "--context-graph",
            "cg-1",
            "--github-token",
            "ghp_x",
            *_COMMON,
        ],
    )
    assert result.exit_code == 1
    assert "VM_PUBLISH_PRECONDITION" in result.output


@respx.mock
def test_endorse_command():
    route = respx.post(f"{_DKG}/api/endorse").mock(
        return_value=httpx.Response(200, json={"queued": True})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["endorse", "did:dkg:otp/0xabc/9", "--context-graph", "cg-1", *_COMMON],
    )
    assert result.exit_code == 0, result.output
    assert "Endorsed did:dkg:otp/0xabc/9" in result.output
    body = json.loads(route.calls.last.request.content)
    assert body == {"contextGraphId": "cg-1", "ual": "did:dkg:otp/0xabc/9"}


@respx.mock
def test_verify_decision_command_verified():
    route = respx.post(f"{_DKG}/api/verify").mock(
        return_value=httpx.Response(
            200, json={"status": "verified", "signatures": ["0xs1", "0xs2"]}
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "verify-decision",
            "vm-1",
            "batch-1",
            "--context-graph",
            "cg-1",
            "--required-signatures",
            "2",
            *_COMMON,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "verified" in result.output
    assert "Signers: 2" in result.output
    body = json.loads(route.calls.last.request.content)
    assert body == {
        "contextGraphId": "cg-1",
        "verifiableMemoryId": "vm-1",
        "batchId": "batch-1",
        "requiredSignatures": 2,
    }


@respx.mock
def test_verify_decision_command_no_quorum():
    """A 409 quorum shortfall is reported as a status, not a traceback."""
    respx.post(f"{_DKG}/api/verify").mock(
        return_value=httpx.Response(409, json={"status": "no_quorum"})
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["verify-decision", "vm-1", "batch-1", "--context-graph", "cg-1", *_COMMON],
    )
    assert result.exit_code == 1
    assert "no_quorum" in result.output


@respx.mock
def test_oracle_command():
    route = respx.post(f"{_DKG}/api/query").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "bindings": [
                        {
                            "s": {"value": "urn:github:owner/repo/pr/2"},
                            "p": {"value": "http://schema.org/name"},
                            "o": {"value": "Adopt monorepo"},
                        }
                    ]
                }
            },
        )
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "oracle",
            "monorepo",
            "--context-graph",
            "cg-1",
            "--min-trust",
            "endorsed",
            "--limit",
            "5",
            *_COMMON,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Adopt monorepo" in result.output
    # Provenance footer
    assert "contextGraphId=cg-1" in result.output
    assert "view=verifiable-memory" in result.output
    assert "minTrust=1 (Endorsed)" in result.output
    body = json.loads(route.calls.last.request.content)
    assert body["view"] == "verifiable-memory"
    assert body["minTrust"] == 1
    assert body["contextGraphId"] == "cg-1"
    assert 'CONTAINS(LCASE(STR(?o)), LCASE("monorepo"))' in body["sparql"]
    assert "LIMIT 5" in body["sparql"]


@respx.mock
def test_oracle_command_rejects_bad_trust_level():
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["oracle", "q", "--context-graph", "cg-1", "--min-trust", "sorta", *_COMMON],
    )
    assert result.exit_code == 2
    assert "min-trust" in result.output

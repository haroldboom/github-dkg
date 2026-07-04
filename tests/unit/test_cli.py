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

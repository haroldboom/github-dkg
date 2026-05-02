"""HTTP client for the DKG v10 node API."""

from __future__ import annotations

import os
from typing import Any

import httpx


class DKGClient:
    """Thin async wrapper around the DKG v10 HTTP API (port 9200).

    All methods raise httpx.HTTPStatusError on non-2xx responses.
    """

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("DKG_BASE_URL", "http://localhost:9200")
        ).rstrip("/")
        token = token or os.environ.get("DKG_TOKEN", "")
        if not token:
            raise ValueError(
                "DKG bearer token required. Pass token= or set DKG_TOKEN env var."
            )
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout

    async def ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                r = await http.get(
                    f"{self.base_url}/api/agents", headers=self._headers
                )
                return r.status_code == 200
        except Exception:
            return False

    async def create_context_graph(self, name: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            r = await http.post(
                f"{self.base_url}/api/context-graph/create",
                headers=self._headers,
                json={"name": name},
            )
            r.raise_for_status()
            return r.json()

    async def memory_turn(
        self,
        context_graph_id: str,
        markdown: str,
        session_uri: str | None = None,
        layer: str = "wm",
        sub_graph_name: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a markdown artifact as a Knowledge Asset in Working Memory."""
        body: dict[str, Any] = {
            "contextGraphId": context_graph_id,
            "markdown": markdown,
            "layer": layer,
        }
        if session_uri:
            body["sessionUri"] = session_uri
        if sub_graph_name:
            body["subGraphName"] = sub_graph_name
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            r = await http.post(
                f"{self.base_url}/api/memory/turn",
                headers=self._headers,
                json=body,
            )
            r.raise_for_status()
            return r.json()

    async def memory_search(
        self,
        context_graph_id: str,
        query: str,
        limit: int = 20,
        memory_layers: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "contextGraphId": context_graph_id,
            "query": query,
            "limit": limit,
        }
        if memory_layers:
            body["memoryLayers"] = memory_layers
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            r = await http.post(
                f"{self.base_url}/api/memory/search",
                headers=self._headers,
                json=body,
            )
            r.raise_for_status()
            return r.json()

    async def assertion_promote(
        self,
        name: str,
        context_graph_id: str,
        entities: list[str] | None = None,
    ) -> dict[str, Any]:
        """Promote a Working Memory assertion to Shared Working Memory (SHARE)."""
        body: dict[str, Any] = {"contextGraphId": context_graph_id}
        if entities:
            body["entities"] = entities
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            r = await http.post(
                f"{self.base_url}/api/assertion/{name}/promote",
                headers=self._headers,
                json=body,
            )
            r.raise_for_status()
            return r.json()

    async def query(
        self,
        sparql: str,
        include_workspace: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "sparql": sparql,
            "includeWorkspace": include_workspace,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            r = await http.post(
                f"{self.base_url}/api/query",
                headers=self._headers,
                json=body,
            )
            r.raise_for_status()
            return r.json()

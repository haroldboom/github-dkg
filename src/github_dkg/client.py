"""HTTP client for the DKG v10 node API."""

from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import quote

import httpx

_DEFAULT_MEMORY_LAYERS = ["wm", "swm"]

# Terminal promote-async job states (see PROMOTE_JOB_STATES in the node's
# dkg-publisher package): queued/running/failed_retrying are non-terminal.
_PROMOTE_SUCCEEDED = "succeeded"
_PROMOTE_FAILED = "failed"


class DKGPromoteError(RuntimeError):
    """Raised when an async promote job fails or does not finish in time.

    Attributes:
        job: The last job view returned by the node (may be None if the
            submission itself produced no job).
    """

    def __init__(self, message: str, job: dict[str, Any] | None = None) -> None:
        self.job = job
        super().__init__(message)


class DKGClient:
    """Thin async wrapper around the DKG v10 HTTP API (port 9200).

    All methods raise httpx.HTTPStatusError on non-2xx responses. A single
    connection-pooled httpx.AsyncClient is shared across calls; call
    ``aclose()`` when done (optional — the pool is recreated per event loop).
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
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    def _http(self) -> httpx.AsyncClient:
        """Return the shared connection-pooled client for the running loop.

        Cached together with the event loop it was created on; recreated if
        the running loop changed (e.g. successive ``asyncio.run`` calls).
        """
        loop = asyncio.get_running_loop()
        if (
            self._client is None
            or self._client.is_closed
            or self._client_loop is not loop
        ):
            self._client = httpx.AsyncClient(
                timeout=self._timeout, headers=self._headers
            )
            self._client_loop = loop
        return self._client

    async def aclose(self) -> None:
        """Close the pooled HTTP client (if any)."""
        client, self._client, self._client_loop = self._client, None, None
        if client is not None and not client.is_closed:
            await client.aclose()

    async def ping(self) -> bool:
        """Probe the node with GET /api/context-graph/list.

        Returns True when the node answers 200. Returns False only on
        transport-level errors (node unreachable). HTTP error statuses
        (e.g. 401 for a bad token) raise httpx.HTTPStatusError so callers
        can distinguish "unreachable" from "rejected".
        """
        try:
            r = await self._http().get(f"{self.base_url}/api/context-graph/list")
        except httpx.TransportError:
            return False
        r.raise_for_status()
        return True

    async def create_context_graph(
        self, name: str, id: str | None = None
    ) -> dict[str, Any]:
        """Create a context graph. The node requires both ``id`` and ``name``;
        when ``id`` is omitted the name is used as the id."""
        r = await self._http().post(
            f"{self.base_url}/api/context-graph/create",
            json={"id": id or name, "name": name},
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    async def memory_turn(
        self,
        context_graph_id: str,
        markdown: str,
        session_uri: str | None = None,
        layer: str = "wm",
        sub_graph_name: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a markdown artifact as a Knowledge Asset in Working Memory.

        ``sub_graph_name`` must not contain "/". Writing the same
        sub_graph_name twice does not deduplicate — each write creates a new
        timestamped turn grouped under that sub-graph.
        """
        body: dict[str, Any] = {
            "contextGraphId": context_graph_id,
            "markdown": markdown,
            "layer": layer,
        }
        if session_uri:
            body["sessionUri"] = session_uri
        if sub_graph_name:
            body["subGraphName"] = sub_graph_name
        r = await self._http().post(
            f"{self.base_url}/api/memory/turn",
            json=body,
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    async def memory_search(
        self,
        context_graph_id: str,
        query: str,
        limit: int = 20,
        memory_layers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search memory. ``memoryLayers`` is always sent: current node builds
        return 0 results when it is omitted, so None defaults to ["wm", "swm"].
        Pass an explicit list (including []) to send it verbatim."""
        body: dict[str, Any] = {
            "contextGraphId": context_graph_id,
            "query": query,
            "limit": limit,
            "memoryLayers": (
                memory_layers if memory_layers is not None else _DEFAULT_MEMORY_LAYERS
            ),
        }
        r = await self._http().post(
            f"{self.base_url}/api/memory/search",
            json=body,
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

    async def assertion_promote(
        self,
        name: str,
        context_graph_id: str,
        entities: list[str] | None = None,
        sub_graph_name: str | None = None,
        poll_timeout: float = 30.0,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        """Promote a Working Memory assertion to Shared Working Memory (SHARE).

        Uses the node's async promote flow: submits
        POST /api/knowledge-assets/{name}/swm/share-async, then polls
        GET /api/knowledge-assets/swm/share-jobs/{jobId} every
        ``poll_interval`` seconds until the job succeeds, fails, or
        ``poll_timeout`` elapses. Older node builds that 404 the
        knowledge-assets surface fall back to the legacy
        /api/assertion/{name}/promote-async routes.

        Returns the final job view on success. Raises DKGPromoteError when
        the job fails or does not reach a terminal state within
        ``poll_timeout``. A 409 on submission (job already in flight) polls
        the existing job instead.
        """
        body: dict[str, Any] = {"contextGraphId": context_graph_id}
        if entities:
            body["entities"] = entities
        if sub_graph_name:
            body["subGraphName"] = sub_graph_name
        quoted = quote(name, safe="")
        legacy = False
        r = await self._http().post(
            f"{self.base_url}/api/knowledge-assets/{quoted}/swm/share-async",
            json=body,
        )
        if r.status_code == 404:
            legacy = True
            r = await self._http().post(
                f"{self.base_url}/api/assertion/{quoted}/promote-async",
                json=body,
            )
        if r.status_code == 409:
            job_id = r.json().get("existingJobId")
        else:
            r.raise_for_status()
            job_id = r.json().get("jobId")
        if not job_id:
            raise DKGPromoteError(
                f"promote-async for {name!r} returned no job id: {r.text[:200]}"
            )

        poll_url = (
            f"{self.base_url}/api/assertion/promote-async/{job_id}"
            if legacy
            else f"{self.base_url}/api/knowledge-assets/swm/share-jobs/{job_id}"
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + poll_timeout
        while True:
            pr = await self._http().get(poll_url)
            pr.raise_for_status()
            job: dict[str, Any] = pr.json()
            state = job.get("state")
            if state == _PROMOTE_SUCCEEDED:
                return job
            if state == _PROMOTE_FAILED:
                raise DKGPromoteError(
                    f"promote job {job_id} for {name!r} failed: "
                    f"{job.get('error') or job}",
                    job=job,
                )
            if loop.time() >= deadline:
                raise DKGPromoteError(
                    f"promote job {job_id} for {name!r} did not finish within "
                    f"{poll_timeout}s (last state: {state})",
                    job=job,
                )
            await asyncio.sleep(poll_interval)

    async def query(
        self,
        sparql: str,
        include_workspace: bool = True,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "sparql": sparql,
            "includeWorkspace": include_workspace,
        }
        r = await self._http().post(
            f"{self.base_url}/api/query",
            json=body,
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
        return data

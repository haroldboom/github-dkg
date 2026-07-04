"""Build RDF quads for decision-bearing GitHub items (Verifiable Memory)."""

from __future__ import annotations

from typing import Any

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_SCHEMA = "http://schema.org/"

# schema:text is truncated so a single decision stays well under node quad
# size limits; the canonical full text remains on GitHub at schema:url.
_MAX_TEXT_CHARS = 4000


def decision_subject(owner: str, repo: str, kind: str, number: int) -> str:
    """Stable URN for a decision-bearing issue or PR."""
    return f"urn:github:{owner}/{repo}/{kind}/{number}"


def decision_to_quads(
    item: dict[str, Any], owner: str, repo: str, kind: str
) -> list[dict[str, Any]]:
    """Render a GitHub issue or PR as minimal schema.org decision quads.

    ``kind`` is "issue" or "pr". The subject is
    ``urn:github:{owner}/{repo}/{kind}/{n}``; optional fields (author, dates,
    body) are omitted when missing rather than emitted empty.
    """
    if kind not in ("issue", "pr"):
        raise ValueError(f"kind must be 'issue' or 'pr', got {kind!r}")
    number = item["number"]
    subject = decision_subject(owner, repo, kind, number)

    def quad(predicate: str, obj: str) -> dict[str, Any]:
        return {"subject": subject, "predicate": predicate, "object": obj}

    quads = [
        quad(_RDF_TYPE, f"{_SCHEMA}CreativeWork"),
        quad(f"{_SCHEMA}name", item.get("title") or f"{kind} #{number}"),
        quad(
            f"{_SCHEMA}url",
            item.get("html_url")
            or f"https://github.com/{owner}/{repo}/"
            f"{'pull' if kind == 'pr' else 'issues'}/{number}",
        ),
        quad(f"{_SCHEMA}isPartOf", f"urn:github:{owner}/{repo}"),
    ]

    author = (item.get("user") or {}).get("login")
    if author:
        quads.append(quad(f"{_SCHEMA}author", author))
    if created := item.get("created_at"):
        quads.append(quad(f"{_SCHEMA}dateCreated", created))
    # The decision date: when the PR merged, else when the item closed.
    if published := item.get("merged_at") or item.get("closed_at"):
        quads.append(quad(f"{_SCHEMA}datePublished", published))
    if body := (item.get("body") or "").strip():
        quads.append(quad(f"{_SCHEMA}text", body[:_MAX_TEXT_CHARS]))
    return quads

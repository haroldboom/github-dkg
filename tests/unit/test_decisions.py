"""Unit tests for decision_to_quads (Verifiable Memory decision publishing)."""

import pytest

from github_dkg.decisions import decision_to_quads

_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"

FAKE_PR = {
    "number": 42,
    "title": "Adopt hexagonal architecture",
    "user": {"login": "alice"},
    "created_at": "2024-01-02T00:00:00Z",
    "merged_at": "2024-01-05T00:00:00Z",
    "closed_at": "2024-01-05T00:00:00Z",
    "body": "We decided to restructure the service boundaries.",
    "html_url": "https://github.com/owner/repo/pull/42",
}


def _by_predicate(quads):
    return {q["predicate"]: q["object"] for q in quads}


def test_decision_to_quads_shape():
    quads = decision_to_quads(FAKE_PR, "owner", "repo", "pr")
    subject = "urn:github:owner/repo/pr/42"
    assert all(set(q) == {"subject", "predicate", "object"} for q in quads)
    assert all(q["subject"] == subject for q in quads)

    objs = _by_predicate(quads)
    assert objs[_RDF_TYPE] == "http://schema.org/CreativeWork"
    assert objs["http://schema.org/name"] == "Adopt hexagonal architecture"
    assert objs["http://schema.org/url"] == "https://github.com/owner/repo/pull/42"
    assert objs["http://schema.org/author"] == "alice"
    assert objs["http://schema.org/dateCreated"] == "2024-01-02T00:00:00Z"
    # merged_at wins over closed_at for the decision date
    assert objs["http://schema.org/datePublished"] == "2024-01-05T00:00:00Z"
    assert objs["http://schema.org/text"] == FAKE_PR["body"]
    assert objs["http://schema.org/isPartOf"] == "urn:github:owner/repo"


def test_decision_to_quads_issue_uses_closed_at():
    issue = {
        "number": 7,
        "title": "Drop Python 3.9",
        "user": {"login": "bob"},
        "created_at": "2024-02-01T00:00:00Z",
        "closed_at": "2024-02-03T00:00:00Z",
        "body": "Decision: 3.10 is the new floor.",
        "html_url": "https://github.com/owner/repo/issues/7",
    }
    objs = _by_predicate(decision_to_quads(issue, "owner", "repo", "issue"))
    assert objs["http://schema.org/datePublished"] == "2024-02-03T00:00:00Z"
    quads = decision_to_quads(issue, "owner", "repo", "issue")
    assert quads[0]["subject"] == "urn:github:owner/repo/issue/7"


def test_decision_to_quads_omits_missing_optionals():
    minimal = {"number": 1, "title": "T", "html_url": "https://x", "body": None}
    objs = _by_predicate(decision_to_quads(minimal, "o", "r", "issue"))
    assert "http://schema.org/author" not in objs
    assert "http://schema.org/dateCreated" not in objs
    assert "http://schema.org/datePublished" not in objs
    assert "http://schema.org/text" not in objs


def test_decision_to_quads_truncates_body():
    long_body = "x" * 10_000
    objs = _by_predicate(
        decision_to_quads(
            {"number": 1, "title": "T", "body": long_body}, "o", "r", "issue"
        )
    )
    assert len(objs["http://schema.org/text"]) == 4000


def test_decision_to_quads_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind must be"):
        decision_to_quads({"number": 1}, "o", "r", "discussion")

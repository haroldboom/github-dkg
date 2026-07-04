"""Unit tests for the Markdown formatter."""

from github_dkg.formatter import format_issue, format_pull_request


ISSUE = {
    "number": 42,
    "title": "Fix null pointer in auth flow",
    "user": {"login": "alice"},
    "labels": [{"name": "bug"}, {"name": "priority-high"}],
    "state": "closed",
    "state_reason": "completed",
    "created_at": "2024-03-01T10:00:00Z",
    "closed_at": "2024-03-05T14:00:00Z",
    "body": "The login endpoint returns 500 when password contains `$`.",
    "html_url": "https://github.com/acme/api/issues/42",
}

COMMENTS = [
    {
        "user": {"login": "bob"},
        "created_at": "2024-03-02T09:00:00Z",
        "body": "Reproduced on 2.3.1.",
    },
    {
        "user": {"login": "alice"},
        "created_at": "2024-03-04T11:00:00Z",
        "body": "Fixed in commit abc123.",
    },
]

PR = {
    "number": 99,
    "title": "Add PKCE support for mobile OAuth",
    "user": {"login": "carol"},
    "labels": [{"name": "feature"}],
    "state": "merged",
    "draft": False,
    "created_at": "2024-03-10T08:00:00Z",
    "merged_at": "2024-03-15T16:00:00Z",
    "closed_at": "2024-03-15T16:00:00Z",
    "body": "Implements RFC 7636 PKCE for public clients.",
    "html_url": "https://github.com/acme/api/pull/99",
    "base": {"ref": "main"},
    "head": {"ref": "feature/pkce"},
    "requested_reviewers": [{"login": "dave"}],
}

REVIEWS = [
    {
        "user": {"login": "dave"},
        "state": "APPROVED",
        "body": "LGTM, clean implementation.",
        "submitted_at": "2024-03-14T12:00:00Z",
    }
]

INLINE_COMMENTS = [
    {
        "user": {"login": "dave"},
        "path": "src/auth/pkce.py",
        "body": "Consider caching the verifier.",
    }
]


def test_format_issue_contains_key_fields():
    md = format_issue(ISSUE, COMMENTS, "acme", "api")
    assert "**GitHub Issue #42:**" in md
    assert "Fix null pointer in auth flow" in md
    assert "alice" in md
    assert "bug" in md
    assert "priority-high" in md
    assert "closed" in md
    assert "completed" in md
    assert "2024-03-01" in md
    assert "2024-03-05" in md
    assert "The login endpoint returns 500" in md


def test_format_issue_includes_comments():
    md = format_issue(ISSUE, COMMENTS, "acme", "api")
    assert "bob" in md
    assert "Reproduced on 2.3.1." in md
    assert "Fixed in commit abc123." in md


def test_format_issue_no_body():
    issue = {**ISSUE, "body": None}
    md = format_issue(issue, [], "acme", "api")
    assert "**GitHub Issue #42:**" in md
    assert "**Description:**" not in md


def test_format_issue_no_comments():
    md = format_issue(ISSUE, [], "acme", "api")
    assert "**Comments:**" not in md


def test_format_pull_request_contains_key_fields():
    md = format_pull_request(PR, REVIEWS, INLINE_COMMENTS, "acme", "api")
    assert "**GitHub PR #99:**" in md
    assert "Add PKCE support" in md
    assert "carol" in md
    assert "feature" in md
    assert "merged" in md
    assert "2024-03-10" in md
    assert "2024-03-15" in md
    assert "feature/pkce → main" in md
    assert "RFC 7636" in md


def test_format_pull_request_includes_reviews():
    md = format_pull_request(PR, REVIEWS, INLINE_COMMENTS, "acme", "api")
    assert "dave" in md
    assert "APPROVED" in md
    assert "LGTM" in md


def test_format_pull_request_includes_inline_comments():
    md = format_pull_request(PR, REVIEWS, INLINE_COMMENTS, "acme", "api")
    assert "src/auth/pkce.py" in md
    assert "Consider caching the verifier." in md


def test_format_pull_request_no_reviews():
    md = format_pull_request(PR, [], [], "acme", "api")
    assert "**Reviews:**" not in md
    assert "**Inline review comments:**" not in md


def test_format_issue_no_labels():
    issue = {**ISSUE, "labels": []}
    md = format_issue(issue, [], "acme", "api")
    assert "none" in md


def test_format_issue_missing_user():
    issue = {**ISSUE, "user": None}
    md = format_issue(issue, [], "acme", "api")
    assert "unknown" in md


def test_format_issue_truncation_marker():
    md = format_issue(ISSUE, COMMENTS, "acme", "api", total_comments=7)
    assert "… 5 more comment(s) omitted" in md


def test_format_issue_no_truncation_marker_when_all_shown():
    md = format_issue(ISSUE, COMMENTS, "acme", "api", total_comments=len(COMMENTS))
    assert "omitted" not in md


def test_format_issue_no_truncation_marker_without_total():
    md = format_issue(ISSUE, COMMENTS, "acme", "api")
    assert "omitted" not in md


def test_format_pull_request_truncation_marker():
    md = format_pull_request(PR, REVIEWS, [], "acme", "api", total_reviews=4)
    assert "… 3 more review(s) omitted" in md


def test_multiline_comment_body_cannot_fake_attribution():
    """Continuation lines of a comment body are indented so a crafted body
    cannot inject top-level '**Author:**'-style lines."""
    evil = {
        "user": {"login": "mallory"},
        "created_at": "2024-03-02T09:00:00Z",
        "body": "innocuous first line\n**Author:** admin  |  **Labels:** trusted",
    }
    md = format_issue(ISSUE, [evil], "acme", "api")
    lines = md.splitlines()
    # The injected line must not appear at column 0.
    assert "**Author:** admin  |  **Labels:** trusted" not in lines
    assert "  **Author:** admin  |  **Labels:** trusted" in lines


def test_single_line_comment_rendering_unchanged():
    md = format_issue(ISSUE, COMMENTS, "acme", "api")
    assert "- **bob** (2024-03-02): Reproduced on 2.3.1." in md.splitlines()


def test_multiline_review_body_is_indented():
    review = {
        "user": {"login": "mallory"},
        "state": "COMMENTED",
        "submitted_at": "2024-03-14T12:00:00Z",
        "body": "first\n**GitHub PR #1:** fake",
    }
    md = format_pull_request(PR, [review], [], "acme", "api")
    lines = md.splitlines()
    assert "**GitHub PR #1:** fake" not in lines
    assert "  **GitHub PR #1:** fake" in lines

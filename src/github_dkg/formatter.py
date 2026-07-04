"""Format GitHub items as Markdown Knowledge Assets for DKG v10 Working Memory."""

from __future__ import annotations

from typing import Any


def _label_names(labels: list[dict[str, Any]]) -> str:
    names = [lbl.get("name", "") for lbl in labels if lbl.get("name")]
    return ", ".join(names) if names else "none"


def _username(user: dict[str, Any] | None) -> str:
    if not user:
        return "unknown"
    return user.get("login", "unknown")


def _indent_continuation(text: str, indent: str = "  ") -> str:
    """Indent every line after the first so multi-line user-supplied bodies
    cannot fake top-level attribution lines (e.g. a "**Author:** ..." line).

    Single-line text is returned unchanged.
    """
    lines = text.splitlines()
    if len(lines) <= 1:
        return text
    return "\n".join([lines[0], *(f"{indent}{line}" for line in lines[1:])])


def format_issue(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    owner: str,
    repo: str,
    total_comments: int | None = None,
) -> str:
    """Render an issue (plus up to the given comments) as Markdown.

    ``total_comments`` is the number of comments that exist on the issue
    (as known by the caller). When it exceeds ``len(comments)`` a
    "… N more comment(s) omitted" marker is appended.
    """
    number = issue["number"]
    title = issue.get("title", "")
    author = _username(issue.get("user"))
    labels = _label_names(issue.get("labels", []))
    state = issue.get("state", "unknown")
    state_reason = issue.get("state_reason") or ""
    created = (issue.get("created_at") or "")[:10]
    closed = (issue.get("closed_at") or "")[:10]
    body = (issue.get("body") or "").strip()
    url = issue.get("html_url", f"https://github.com/{owner}/{repo}/issues/{number}")

    lines = [
        f"**GitHub Issue #{number}:** {title}",
        f"**Repository:** {owner}/{repo}",
        f"**Author:** {author}  |  **Labels:** {labels}  |  **State:** {state}"
        + (f" ({state_reason})" if state_reason else ""),
        f"**Created:** {created}" + (f"  |  **Closed:** {closed}" if closed else ""),
        f"**URL:** {url}",
    ]

    if body:
        lines += ["", "**Description:**", body]

    if comments:
        lines += ["", "**Comments:**"]
        for c in comments:
            commenter = _username(c.get("user"))
            when = (c.get("created_at") or "")[:10]
            text = (c.get("body") or "").strip()
            if text:
                lines += [
                    f"- **{commenter}** ({when}): {_indent_continuation(text)}"
                ]
        if total_comments is not None and total_comments > len(comments):
            omitted = total_comments - len(comments)
            lines.append(f"- … {omitted} more comment(s) omitted")

    return "\n".join(lines)


def format_pull_request(
    pr: dict[str, Any],
    reviews: list[dict[str, Any]],
    inline_comments: list[dict[str, Any]],
    owner: str,
    repo: str,
    total_reviews: int | None = None,
) -> str:
    """Render a PR (plus up to the given reviews/inline comments) as Markdown.

    ``total_reviews`` is the number of reviews that exist on the PR (as known
    by the caller). When it exceeds ``len(reviews)`` a
    "… N more review(s) omitted" marker is appended.
    """
    number = pr["number"]
    title = pr.get("title", "")
    author = _username(pr.get("user"))
    labels = _label_names(pr.get("labels", []))
    state = pr.get("state", "unknown")
    draft = " (draft)" if pr.get("draft") else ""
    created = (pr.get("created_at") or "")[:10]
    merged = (pr.get("merged_at") or "")[:10]
    closed = (pr.get("closed_at") or "")[:10]
    body = (pr.get("body") or "").strip()
    url = pr.get("html_url", f"https://github.com/{owner}/{repo}/pull/{number}")

    base_ref = pr.get("base", {}).get("ref", "")
    head_ref = pr.get("head", {}).get("ref", "")
    branch_line = f"**Branch:** {head_ref} → {base_ref}" if base_ref or head_ref else ""

    requested_reviewers = [
        _username(r) for r in pr.get("requested_reviewers", [])
    ]
    reviewer_str = ", ".join(requested_reviewers) if requested_reviewers else ""

    lines = [
        f"**GitHub PR #{number}:** {title}{draft}",
        f"**Repository:** {owner}/{repo}",
        f"**Author:** {author}  |  **Labels:** {labels}  |  **State:** {state}",
    ]
    if reviewer_str:
        lines.append(f"**Requested reviewers:** {reviewer_str}")
    if branch_line:
        lines.append(branch_line)
    lines.append(
        f"**Created:** {created}"
        + (f"  |  **Merged:** {merged}" if merged else "")
        + (f"  |  **Closed:** {closed}" if closed and not merged else "")
    )
    lines.append(f"**URL:** {url}")

    if body:
        lines += ["", "**Description:**", body]

    if reviews:
        lines += ["", "**Reviews:**"]
        for rev in reviews:
            reviewer = _username(rev.get("user"))
            rev_state = rev.get("state", "")
            rev_body = (rev.get("body") or "").strip()
            submitted = (rev.get("submitted_at") or "")[:10]
            summary = f"- **{reviewer}** {rev_state} ({submitted})"
            if rev_body:
                summary += f": {_indent_continuation(rev_body)}"
            lines.append(summary)
        if total_reviews is not None and total_reviews > len(reviews):
            omitted = total_reviews - len(reviews)
            lines.append(f"- … {omitted} more review(s) omitted")

    # Aggregate inline review comments by file path
    if inline_comments:
        by_path: dict[str, list[str]] = {}
        for ic in inline_comments:
            path = ic.get("path", "unknown")
            commenter = _username(ic.get("user"))
            text = (ic.get("body") or "").strip()
            if text:
                by_path.setdefault(path, []).append(
                    f"{commenter}: {_indent_continuation(text, indent='    ')}"
                )
        if by_path:
            lines += ["", "**Inline review comments:**"]
            for path, cmts in by_path.items():
                lines.append(f"- `{path}`:")
                for cmt in cmts:
                    lines.append(f"  - {cmt}")

    return "\n".join(lines)

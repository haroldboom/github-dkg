# github-dkg Design Brief

**Package:** `github-dkg`  
**Bounty tag:** `cfi-dkgv10-r1`  
**Tier target:** Flagship (8,000–10,000 TRAC)

---

## 1. Problem

Software teams produce tacit knowledge continuously — in issue threads, PR descriptions, code review comments, post-mortem discussions. This knowledge evaporates the moment an issue is closed or a PR merges. It lives in GitHub, locked away from agents.

Existing retrieval approaches (GitHub search, grep, CI logs) can surface raw text, but they provide:
- No provenance — who decided what, and when
- No trust gradient — a passing remark and an architecture decision look identical
- No agent-native interface — agents cannot treat GitHub as a queryable knowledge substrate

DKG v10 Working Memory solves all three. This package ingests GitHub's knowledge stream into DKG v10, where it becomes attributable, queryable, and promotable toward on-chain verification.

---

## 2. Target users

- **Platform and DevOps teams** who want their engineering knowledge base to accumulate passively, without manual curation
- **Research-engineering teams** running long-horizon agentic workflows (code analysis, architecture review, dependency audits) that need to query what the team previously decided
- **Multi-agent systems** that coordinate across a repository: an agent can write a PR review into Shared Working Memory and a downstream agent can query it to inform its next action

---

## 3. Architecture

```
GitHub Repository
        │
        ├─ Issues (+ comments)
        ├─ Pull Requests (+ reviews + inline comments)
        │
        ▼
GitHubDKGIngestor
        │
        ├─ GitHubClient ──────────────► GitHub REST API v3
        │   (issues, pulls, reviews,     (unauthenticated rate-limited
        │    inline comments)             or authenticated via GITHUB_TOKEN)
        │
        ├─ MarkdownFormatter ─────────► Structured Markdown Knowledge Asset
        │   (one KA per issue/PR,         per item, with code-aware tagging
        │    comments embedded)           and provenance metadata)
        │
        └─ DKGClient ─────────────────► POST /api/memory/turn
                                         (one Knowledge Asset per item,
                                          scoped to a repo Context Graph,
                                          sessionUri = github.com/owner/repo)

GitHub Action (Docker)
        │
        └─ Triggered on: issues, pull_request, pull_request_review events
           Reads: GITHUB_EVENT_PATH payload → item number → ingest
           Writes: GITHUB_OUTPUT turn-uri, layer
```

### API surface used

All communication is over the public DKG v10 HTTP API — no internal packages.

| Endpoint | Purpose |
|---|---|
| `GET /api/agents` | Health check / token validation |
| `POST /api/memory/turn` | Write Knowledge Asset (one per issue/PR) |
| `POST /api/memory/search` | Tri-modal search across ingested knowledge |
| `POST /api/assertion/:name/promote` | SHARE to Shared Working Memory |

---

## 4. Memory layer mapping and LLM-Wiki alignment

Karpathy's LLM-Wiki frames knowledge substrates by who can read and write them. GitHub is where engineering teams produce knowledge that currently has no agent-native substrate. This package maps GitHub's knowledge types to the v10 trust gradient:

| GitHub artifact | DKG v10 layer | Default | Promotion trigger |
|---|---|---|---|
| Open issue | Working Memory | `wm` | Team label e.g. `architecture-decision` |
| Closed issue | Working Memory | `wm` | Post-mortem label or manual promote |
| Draft PR | Working Memory | `wm` | — |
| Merged PR | Working Memory | `wm` | Label `architecture-decision`, or manual |
| Review comment (APPROVED) | Working Memory | `wm` | PR merge + label |

The **sessionUri** for every Knowledge Asset is set to `https://github.com/owner/repo`, linking all assets for a repository into a coherent session in the Context Graph. This allows an agent to retrieve all knowledge about a repository in a single search.

---

## 5. Trust gradient and promotion path

### Working Memory → Shared Working Memory (SHARE)

An engineering team's GitHub repo is a natural unit of Shared Memory. When a significant PR merges — one labelled `architecture-decision`, or one identified by an agent as high-signal — the workflow promotes its Knowledge Asset:

```bash
github-dkg promote dkg://wm/turn/abc123 --context-graph my-project
```

This calls `POST /api/assertion/:name/promote` — a Curator-authorized operation. Nothing is promoted automatically; the agent or CI pipeline must explicitly decide.

The example workflow in `examples/workflow.yml` shows a concrete trigger: PRs with the `architecture-decision` label are automatically promoted on merge.

### Shared Working Memory → Verified Memory (PUBLISH)

Round 2 surface. Once an architecture decision or post-mortem is in Shared Working Memory, it can be published to Verified Memory via `POST /api/shared-memory/publish`. The UAL chain is preserved through all promotions: the on-chain record traces back to the original GitHub issue or PR, preserving full provenance.

### Oracle-readiness

Every Knowledge Asset written by this package:
- Has a stable UAL (the `turnUri` returned by `/api/memory/turn`)
- Is scoped to a Context Graph, making it consumable by a context oracle querying that graph
- Uses `sessionUri` to link assets for a repository, enabling oracle queries like "all architecture decisions for repo X"
- Structured Markdown with explicit field headers (`**Author:**`, `**Labels:**`, `**State:**`) produces consistent RDF triples, making semantic queries predictable

---

## 6. Knowledge Asset format

Each GitHub item is encoded as structured Markdown before ingestion. The DKG node runs structural + semantic extraction on this Markdown, building RDF triples from the field headers and free-text content.

**Issue example:**

```markdown
**GitHub Issue #42:** Fix null pointer in auth flow
**Repository:** acme/api
**Author:** alice  |  **Labels:** bug, priority-high  |  **State:** closed (completed)
**Created:** 2024-03-01  |  **Closed:** 2024-03-05
**URL:** https://github.com/acme/api/issues/42

**Description:**
The login endpoint returns 500 when password contains `$`.

**Comments:**
- **bob** (2024-03-02): Reproduced on 2.3.1.
- **alice** (2024-03-04): Fixed in commit abc123.
```

**PR example:**

```markdown
**GitHub PR #99:** Add PKCE support for mobile OAuth
**Repository:** acme/api
**Author:** carol  |  **Labels:** feature  |  **State:** merged
**Requested reviewers:** dave
**Branch:** feature/pkce → main
**Created:** 2024-03-10  |  **Merged:** 2024-03-15
**URL:** https://github.com/acme/api/pull/99

**Description:**
Implements RFC 7636 PKCE for public clients.

**Reviews:**
- **dave** APPROVED (2024-03-14): LGTM, clean implementation.

**Inline review comments:**
- `src/auth/pkce.py`:
  - dave: Consider caching the verifier.
```

---

## 7. v10 vocabulary compliance

All code and documentation uses the exact DKG v10 terminology:

- **Context Graph** — one per repository, scoping all Knowledge Assets for that repo
- **Knowledge Asset** — one per GitHub issue or PR
- **Working Memory** / **Shared Working Memory** / **Verified Memory** — never "private/public/chain"
- **SHARE** — for promotion to Shared Working Memory
- **PUBLISH** — for promotion to Verified Memory (Round 2)
- **Curator** — the authority required for SHARE/PUBLISH operations

One intentional deviation: the CLI uses `layer` as shorthand for `--layer wm|swm` as a usability affordance for operators. Internal code and documentation always expands this to the full v10 term.

---

## 8. Security notes

- All credentials (`DKG_TOKEN`, `GITHUB_TOKEN`) are read from environment variables — never hardcoded or logged
- No Curator operations (SHARE/PUBLISH) are performed automatically; all promotion is explicit and operator-initiated
- The Docker action image has no `postinstall` or `preinstall` scripts
- Network egress: GitHub REST API (`api.github.com`) and the configured DKG node endpoint — no other external domains
- Write authority: only `POST /api/memory/turn` (write Working Memory) and `POST /api/assertion/:name/promote` (SHARE, Curator-authorized). No chain-write operations.
- No dynamic code loading, no `eval` on external input
- The `GITHUB_TOKEN` used in the Action has the minimum required permissions: `contents: read`, `issues: read`, `pull-requests: read`

---

## 9. Maintenance commitment

Six-month support window from submission date. Issues and pull requests will be reviewed within 5 business days. The package follows semantic versioning; breaking changes will be major version bumps with migration notes.

# github-dkg 0.2 Design Brief — verified engineering decisions

**Package:** `github-dkg` (0.2)
**Bounty tag:** `cfi-dkgv10-r2` (provisional — this brief targets the roadmapped Round 2 scope, *Verifiable Memory & context oracles*, and will be tuned when the round officially opens)
**Round 1 foundation:** `github-dkg` 0.1 — registry entry in OriginTrail/dkg-integrations. Follow-on submission: same package, same maintainer, new version.

---

## 1. Problem

Round 1 ingested a repository's tacit knowledge — issues, PRs, reviews — into Working Memory, and let teams SHARE the significant items. But the artifacts that anchor an engineering organisation are its **decisions**: ADRs, "we're doing X" threads, post-mortem conclusions. Today those decisions have no proof layer. Six months later nobody can demonstrate what was decided, by whom, when, or whether the team actually stood behind it — a passing remark and a ratified architecture decision still look identical to any downstream consumer.

DKG v10's Verifiable Memory and trust gradient solve exactly this: a decision can become an on-chain Knowledge Asset (ERC-721, with a UAL) whose trust level climbs as teammates endorse it and an M-of-N verifier quorum co-signs it. `github-dkg` 0.2 wires that gradient to where engineering decisions actually live: GitHub.

---

## 2. Target users

- **Engineering teams with ADR discipline** who want merged decision PRs to become permanent, tamper-evident, on-chain records — automatically, from the label they already apply.
- **Multi-agent engineering systems** where team agents endorse and verify each other's published decisions conversationally, building consensus without any human dashboard.
- **Downstream agents and pipelines** (code-review bots, architecture linters, onboarding assistants) that need to ask "what has this team *verifiably* decided?" and get answers filtered by trust level, with provenance attached.

---

## 3. What 0.2 adds

```
GitHub decision (labelled issue / merged ADR PR)
        │
        ▼
github-dkg publish-decision owner/repo 42 --type pr
        │   fetch → schema.org RDF quads → ka create → wm/write
        │   → wm/finalize (Merkle-sealed, EIP-712 signed)
        │   → swm/share (SHARE) → vm/publish (PUBLISH: on-chain mint)
        ▼   receipt: UAL · txHash · merkleRoot · seal fields
        │
github-dkg endorse <UAL> ─────────────► trust level: Endorsed (1)
github-dkg verify-decision VM BATCH ──► M-of-N quorum → ConsensusVerified (3)
        │
github-dkg oracle "query" --min-trust endorsed
        └── trust-filtered SPARQL over the verifiable-memory view,
            provenance footer on every answer
```

### 3.1 `publish-decision` — decisions become on-chain Knowledge Assets

Takes a decision-bearing issue or PR and runs the full v10 lifecycle in one shot: fetch from GitHub, build minimal **schema.org RDF** quads (`schema:name`, `schema:url`, `schema:author`, `schema:dateCreated`, `schema:datePublished`, `schema:text`, `schema:isPartOf` the repo URN — stable subject `urn:github:{owner}/{repo}/{kind}/{n}`), create the draft Knowledge Asset, write the quads, **finalize** (Merkle root + the author's EIP-712 attestation), **SHARE** to Shared Working Memory, then **PUBLISH** to Verifiable Memory — the on-chain ERC-721 mint. The command prints the UAL, txHash, and merkleRoot; from Python, `GitHubDKGIngestor.publish_decision(...)` returns the publish response merged with the seal fields. The label-driven GitHub Action step (e.g. `adr`, `decision` on merge) makes this hands-off, and the README badge pattern links the resulting UAL from the repo itself.

### 3.2 `endorse` + `verify-decision` — conversational team consensus

`github-dkg endorse <UAL>` writes `dkg:endorses` triples that ride the next publish batch and stamp the asset *Endorsed*. `github-dkg verify-decision VM_ID BATCH_ID --required-signatures 3` requests the M-of-N verifier co-signature quorum that drives a batch to *ConsensusVerified* via on-chain registration; a quorum shortfall is reported as a status (`partial` / `no_quorum`, with signer count, exit code 1), not an error, so CI and agents can poll. Consensus forms the way v10's design principles require — agents and operators acting through tool calls and CLI, **never UI buttons**. A team of review agents can each endorse a decision they participated in, then jointly request verification: the trust gradient becomes a record of who actually stood behind the decision.

### 3.3 `oracle` — repo knowledge as oracle input

`github-dkg oracle "monorepo" --min-trust endorsed` runs trust-filtered SPARQL against the `verifiable-memory` view — the public oracle-consumer read path in build 10.0.2. `--min-trust` accepts `0–3` or names (`selfAttested` … `consensusVerified`). Every answer carries a **provenance footer** (`contextGraphId`, `view`, `minTrust`) so downstream consumers see exactly what trust bar the results cleared. This makes a repository's verified decision record consumable as **oracle input for downstream agents** — the Round 2 criterion of oracle pipelines consuming matured Shared Memory artifacts, applied to engineering knowledge.

---

## 4. Memory layers, v10 primitives, and the promotion path

| Stage | Layer | 0.1 (Round 1) | 0.2 (Round 2) |
|---|---|---|---|
| Ingested issue/PR | Working Memory | `ingest` / Action | unchanged — the feedstock |
| Significant item | Shared Working Memory | label-gated SHARE | SHARE as publish precondition |
| Ratified decision | Verifiable Memory | documented as "Round 2 surface" | **PUBLISH**: sealed, minted, UAL |
| Team endorsement | Verifiable Memory | — | `endorse` → Endorsed |
| Quorum verification | Verifiable Memory | — | `verify-decision` → ConsensusVerified |

This completes the promotion path exactly as the Round 1 brief laid it out: Round 1's ingested Working Memory turns are the raw stream; Round 2 curates the decision-bearing subset and walks it up the full trust gradient — SelfAttested (0) → Endorsed (1) → PartiallyVerified (2) → ConsensusVerified (3). Trust levels are protocol-stamped (user-authored `trustLevel` quads are rejected by the node); the package only ever reports what it reads. The UAL chain is preserved throughout: the on-chain record traces back to the original GitHub URL via `schema:url`, keeping the provenance promise from Round 1.

**LLM-Wiki / autoresearch alignment:** Round 1 gave GitHub knowledge an agent-native external/team memory substrate. 0.2 fills the *long-term knowledge* column: decisions that cleared team consensus become permanent, citable records. An autoresearch or code-analysis agent asking "why is this service event-sourced?" gets an answer with a UAL and the trust level it cleared — not a guess from a stale wiki.

---

## 5. Oracle-readiness and forward compatibility

- Every published decision carries the full provenance bundle: UAL, txHash, merkleRoot, EIP-712 seal fields, and a stable `urn:github:...` subject.
- `DKGClient.kc_metadata(...)` fetches the chain-side Merkle root and author for independent comparison; batch content can be re-checked node-side against the published root.
- Full client-side Merkle inclusion proofs are roadmapped, composed **exclusively from public interfaces** (`query view=verifiable-memory`, `GET /api/kc/:id`, verify-batch, direct RPC reads of the Knowledge Asset storage contract). The bounty rules ban importing internal node packages; we comply strictly — the same constraint discipline as our Round 1 entries.
- The `oracle` command's provenance footer is deliberately machine-readable so a future context-oracle daemon can consume `github-dkg` output unchanged.

---

## 6. Terminology

Exact v10 vocabulary throughout: Context Graph, Knowledge Asset, UAL, Working / Shared Working / **Verifiable** Memory, SHARE, PUBLISH, Curator, trust gradient. Two notes: (1) the Round 1 brief said "Verified Memory"; 0.2 standardizes on **Verifiable Memory** per current v10 materials. (2) The Round 1 CLI shorthand `--layer wm|swm` is retained as a usability affordance; docs always expand it to the full terms.

---

## 7. Status & verification (honest accounting)

- **Implemented and unit-tested:** the full 0.2 surface (`publish-decision`, `endorse`, `verify-decision`, `oracle`, plus the `DKGClient` building blocks `ka_create` / `ka_write` / `ka_finalize` / `vm_publish` / `endorse` / `request_verification` / `kc_metadata` / trust-filtered `query`) ships today with **97 passing tests**, built and verified against node build `10.0.2`.
- **Verified live:** on our Base Sepolia testnet node — Context Graph creation with on-chain registration, Knowledge Asset create, quad write, **finalize (EIP-712 seal confirmed)**, and SWM share completing with `publishReady: true`.
- **Currently blocked, network-side:** the final `vm/publish` step reaches the network ACK stage and fails with `storage_ack_insufficient (0/3)` — every dialled core peer is unresponsive or fails on-chain key verification (`ACK_VERIFY: key-not-registered`). The Base Sepolia core-peer set appears unregistered following the June 29 mainnet launch; publish quorum is unreachable on testnet for all publishers. **Reported to OriginTrail.** Our smoke script re-runs the moment peers re-register; no client-side changes are expected.

---

## 8. Demo plan

Re-using the Round 1 recording pipeline (`examples/demo_video.py` → narrated walkthrough video; `demo.ipynb` mock-backed notebook updated in parallel), once testnet publish quorum is restored:

1. Merge a PR labelled `adr` in a demo repo; the Action step runs `publish-decision` — show the UAL, txHash, and merkleRoot in the job output and the README badge linking the UAL.
2. Two team agents run `endorse` on the UAL, then `verify-decision` — narrate the trust level climbing Endorsed → ConsensusVerified.
3. Run `oracle "why did we choose X" --min-trust consensusVerified` — the decision comes back with its provenance footer; the same query at that floor returns nothing for an unverified decision.

---

## 9. Positioning

Round 1 positioned `github-dkg` as the only GitHub-knowledge ingestion path in the queue, write-side counterpart to our sister submission `langchain-dkg` ([dkg-integrations PR #4](https://github.com/OriginTrail/dkg-integrations/pull/4)). 0.2 preserves that pairing up the trust gradient: decisions published and verified by `github-dkg` are directly retrievable by `langchain-dkg`'s `DKGVerifiedRetriever` at a chosen trust floor — a complete write→verify→consume loop across the two follow-on entries. Same maintainer, same repository, six-month maintenance commitment extended from the 0.2 release date.

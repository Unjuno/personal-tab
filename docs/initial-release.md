# v0.2.0 CPU experimental — initial scope frozen

## Included
- Local single-user/single-active-tab editor with opt-in recording.
- End-of-document inline Ghost Text; Tab accept / Escape dismiss / IME suppression.
- Existing term/statistical predictors; no mandatory ML framework, GPU or model API.
- `spawn` CPU learning/rebuild worker; foreground predictions from an adopted snapshot.
- One running + one pending newest canonical snapshot; canonical edits are not dropped.
- Epoch invalidation on edits/deletions/reset; stale jobs cannot resurrect old learning.
- Explicit plaintext JSON memory export/import; imported provenance is preserved.
- Event trace export, sequential edits, duplicate request handling and size limits.

## Not included / not proved
- Production-quality text prediction, general reasoning, semantic correctness or time savings.
- OS-global input capture, native editor plugins, arbitrary-position next-edit prediction.
- Automatic persistence or encryption; secure RAM erasure; multi-user/multi-tab isolation.
- Background neural-weight training in the live app. GRU stays an opt-in research comparator.
- Docker build and real browser HTTP E2E in the current sandbox: unavailable/blocked.

## API and operational contract
`POST /event` acknowledges a canonical edit without waiting for learning. The result
may use a slightly older snapshot for append-only typing. `POST /suggestion` polls
with doc_id/version/cursor/mode. Old document versions return no candidate. After
a destructive edit no old snapshot is served, even if that temporarily reduces coverage.
HTTP applies a lock to canonical state and prediction; model training and IPC serialization
run outside that lock. This is not a hard real-time guarantee.

Memory restore replaces the whole in-memory session only after schema/size/provenance
validation. It is an explicit user operation. Saved JSON files are not deleted by reset.
Use the snapshot for memory transfer and the trace for diagnostics; they are different files.

`--learning sync` is retained for regression comparisons, not recommended for long documents.
JSON memory controls and polling require the default asynchronous mode.

## Acceptance status
- Functional regression tests: see current test output in the release report.
- CPU latency and final-model equality: measured, workload-specific.
- Predictive quality and real-user usefulness: not a release-success claim.
- Public runtime distribution is a subset of the complete research checkout. Both use
  exactly the same runtime Python/HTML source. Old experiment data and full research
  runners remain in the accompanying research archive; no private inputs are published.

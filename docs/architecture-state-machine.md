# Personal Tab: explicit state machine and architecture contract

Status: design for CPU-only experimental editor, 2026-11-06. The current app only learns from the authorized local editor; its scope is *not* OS-wide input interception.

## Objectives and boundaries

- **User goal:** type ordinarily, see one short, useful continuation, accept with Tab; learn from confirmed self-authored input and adapt over time without requiring a GPU or model API.
- **Non-goals for this increment:** FIM, next edit location, true knowledge reasoning, always-on background key capture, multi-user sync, automatic encrypted persistence, native Japanese IME certification, proof of productivity improvement.
- **Data authority:** the canonical document and origin mask, not the predictor weights or the UI's ghost text. A proposal is never a training target unless the user independently types it.
- **CPU architecture:** browser interaction -> token-protected local HTTP -> versioned canonical session -> worker-bound learning -> immutable/safe snapshot adoption -> suggestion query -> browser guard -> ghost. Worker failure never blocks canonical editing.

## Orthogonal state regions (concurrent FSMs)

The previous implementation effectively encoded these states as multiple mutable flags and sequence counters. The redesign makes each region explicit and defines the precedence of UI rendering.

### Interaction phase

| State | Entry event | Allowed action | Exit |
|---|---|---|---|
| `locked` | first load / consent revoked | opt-in, non-sensitive memory actions | consent granted |
| `ready` | active with no active job | type, query, paste, select | input, composition start, reset/import, error |
| `composing` | IME composition start | edit text; never show/accept Tab proposal | IME composition end -> sync |
| `syncing` | one or more canonical edits unacknowledged | continue typing, no ghost | ack, error, mutation |
| `learning` | latest confirmed edit not yet trained | continue typing; no stale proof of learning | snapshot promotion |
| `offered` | valid candidate, current revision and cursor | Tab, Esc, type, move caret | accepted, dismissed, new edit |
| `degraded` | asynchronous learning worker reports failure | continue canonical editing; do not offer stale candidates | healthy learning result / confirmed reset or memory import |
| `dismissed` | Esc for current sequence | type or change mode | next edit/mode switch |
| `mutating` | reset / memory restore | block edits; wait for canonical queue; reject old responses | operation succeeds -> ready, or error |
| `error` | network/protocol failure | reset/recover only | successful reset |

The order of precedence in the UI is `error > mutating > locked > composing > syncing > degraded > dismissed > offered > learning > ready`. An offered suggestion has a tuple-bound identity: `(request generation, edit sequence, server version, mode, cursor)`; all fields must match current state when displayed and accepted.

### Training phase

| State | Condition | Effect |
|---|---|---|
| `empty` | no canonical edit submitted | no candidate possible |
| `queued` | worker job pending but not yet executing | editor input never waits |
| `training` | background process holds a job | pending result cannot be treated as latest |
| `ready` | current snapshot promoted | can serve learned candidates |
| `updating` | valid older snapshot plus newer append | may serve safe old knowledge; mark pending |
| `rebuilding` | delete/replace/import invalidated previous snapshot | fail closed until matching epoch/versions arrive |
| `failed` | process died or returned error | expose failure; do not pretend learning completed |

A learning snapshot can be promoted only if its `epoch` matches the current epoch, its `request_id` is newer than the current snapshot, and it does not refer to the future. Non-append modifications increment epoch and invalidate all prior candidates. The canonical trace never coalesces; worker *requests* may coalesce because each contains complete confirmed state.

## Invariants

1. Do not mutate canonical text or training counts on IME composition events.
2. New canonical edits are processed sequentially. Each successful acknowledgment must update the local revision even if the rendering generation has since changed (e.g. pause/resume); visual stale guards remain separate.
3. UI mode changes, cursor moves, consent changes, reset/import, and new edits invalidate pending ghost proposals by incrementing render generation or poll serial.
4. Reset/import waits for the edit queue and invalidates old results; it must not replay model-origin or paste-origin text as typed training data.
5. Suggestions are advisory and must not overwrite input without Tab, must not be accepted mid-IME, and must have matching client and server version.
6. Session history cannot be silently expanded to system-wide keystrokes; local input recording requires explicit opt-in.
7. A learning-worker failure must clear any outstanding proposal and show a degraded status while canonical editor input stays enabled. A successful learning response clears that degraded state.
8. Full private data, histories and personalized weights never enter the public Git repository or public CI logs.

## Priority risks from code inspection

- A rapid consent off/on while a request is in-flight can discard the canonical event's acknowledgment and strand the client in an impossible `syncing` phase; solve by separating edit acknowledgment from view generation and resynchronizing on resume.
- The original Ghost Text has no centralized eligibility contract; implement one visible phase derivation and a single current-candidate guard.
- Destructive reset has no confirmation, and memory import can replace existing work; make both explicit, testable user decisions.
- Current per-event snapshot serialization includes all canonical text and an origin tuple copy; p95 latency at maximum corpus size is **not** established; keep as a measured performance risk and do not advertise zero-cost updates.
- All HTTP tabs share one in-memory server state/token; multi-tab editing is not supported. Make this limitation explicit. Multi-tab isolation is a future architectural task, not a claim of this release.

## Verification gates

- TDD regression for off/on while delayed edit response is in-flight, mode and caret cancellation, reset confirmation, import replacement confirmation, and stale snapshot rejection.
- CPU-only full pytest; browser DOM with fake network distinguished from real HTTP; GitHub Actions container, no user data in logs.
- Quantitative output: end-to-end UI tests + Python worker tests; latency measured separately with CPU frequency, batch 1, and document length recorded. No universal performance claims without hardware evidence.

## Next architecture milestones

- Immutable event journal with write-ahead persistence and export/delete guarantees.
- Session/token scope per client tab or explicit multi-tab conflict detection.
- Hot-path optimization: delta-based updates or persistent chunked origin snapshots; keep versioned rebuild for corrections.
- Personalized context-switch learning (stable profile + recent topic) and prospective human adoption study before model changes.

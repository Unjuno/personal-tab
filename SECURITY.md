# Security and privacy

This is a research prototype, not an audited security product.

- Operates only on the explicitly authorized editor, not system-wide input.
- Binds the demo server to 127.0.0.1. Do not proxy it onto a public network.
- HTTP event requests require a session token and an allowed Origin.
- Typed, pasted and model-origin text are tracked separately.
- Data live in process memory. Explicit exports are plaintext; do not commit them.
- Deleting active text rebuilds statistical state but may leave earlier audit events.
  Clearing a session is not a guarantee of physical memory erasure.
- Native IME coverage, undo/redo provenance, encrypted persistence, resource limits,
  multi-user isolation and neural unlearning are not production-validated.
- Never put passwords, tokens or confidential documents into this demo.
- Do not load untrusted pickle/model checkpoint files.

The publication checker is a heuristic, not a guarantee that all secrets are detected.
Report bugs with synthetic, non-sensitive reproductions. Do not post private logs or
working credentials in a public GitHub Issue. Contact the repository owner privately
for sensitive security details; the availability of GitHub private reporting is not assumed.

## v0.2 snapshot persistence

The opt-in memory JSON contains plaintext active user text and provenance, not
pickled weights. Import validates all documents/spans before replacing live state.
Treat both memory JSON and event traces as private. Deleting a server session does
not delete files previously exported to disk. RAM is not securely zeroed. The worker
is a performance boundary, not a sandbox against a malicious local OS user.
The server remains a single-user, single-active-tab research interface.

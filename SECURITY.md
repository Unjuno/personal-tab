# Security and privacy

This is a research prototype, not an audited security product.

- Operates only on the explicitly authorized editor, not system-wide input.
- Binds the demo server to 127.0.0.1. Do not proxy it onto a public network.
- HTTP event requests require a session token and an allowed Origin.
- Typed, pasted and model-origin text are tracked separately.
- Canonical data live in server RAM. When explicitly enabled, browser-side autosave stores AES-256-GCM ciphertext in IndexedDB; the passphrase is not persisted. Manual JSON exports and trace downloads are plaintext. Never commit them.
- Deleting active text rebuilds statistical state but may leave earlier audit events.
  Clearing a session is not a guarantee of physical memory erasure.
- Native IME coverage, undo/redo provenance, physical disk erasure, backup durability,
  cross-device synchronization, multi-user isolation and neural unlearning are not production-validated.
- Do not use passwords or confidential documents as *editor input*. A passphrase for opt-in encrypted autosave is processed only in memory; losing it loses access to the stored ciphertext.
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
The server remains a single-user research interface. Each page receives its own ephemeral capability token and the backend enforces a single-writer lease (60-second expiry; release on page unload). Other tabs cannot submit edits or read snapshots. This is not multi-user access control or device-wide confidentiality.

## v0.3 encrypted browser autosave and learner restart

- AES-256-GCM browser WebCrypto; PBKDF2-HMAC-SHA256 with 310,000 iterations, a random 16-byte salt and a unique 12-byte AES-GCM nonce per snapshot. Data is held in same-origin IndexedDB; passphrases and derived keys are not written there. Encrypted snapshots still carry unavoidable lengths/timestamps. Browser extensions, device compromise, memory inspection, and weak passphrases remain threats.
- Saves are scheduled after acknowledged changes and an idle debounce, not a synchronous write-ahead log. Power loss and abrupt tab closure can lose recent input. Browser site-data deletion destroys backups. The user must remember the passphrase.
- Encrypted backup deletion, and explicit session reset, cannot guarantee physical flash/SSD erasure. Manual plaintext exports remain the user’s responsibility.
- A dead CPU learning process is restarted at most three times with full-snapshot replay on a background thread; persistent failures remain visible rather than silently restarting forever. This is not a malicious-worker sandbox.
- The writer lease is an in-process availability/consistency control, not authentication against a malicious process running on the same operating-system account.

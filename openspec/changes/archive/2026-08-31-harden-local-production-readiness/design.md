## Context

See `proposal.md` for motivation and the delta specs for behavior. Today `DatabasePreflightService` verifies SQLite and managed migration backups, `create_app` composes independent workers and gates, and the dashboard obtains several subsystem snapshots. There is no single typed authority that answers which operational profile currently passes. Migration-backup retention already has a safe preview/quarantine path, while `storage/exports`, `storage/privacy-backups`, `storage/imports` and existing quarantine directories do not share one inventory or lifecycle.

Uvicorn owns the administrator port before FastAPI lifespan runs; NapCat and model providers are separate trust boundaries. The application runs on Windows where sleep, wall-clock jumps, reparse points, file locks, port rebinding and abrupt process exit are routine. SQLite remains authoritative for durable workflow state; `.env`, the dashboard and previously rendered status are never authorization evidence.

## Goals / Non-Goals

**Goals:**

- Produce one typed, bot- and process-scoped readiness decision from independently testable probes without weakening existing gates.
- Revalidate effect-critical evidence at the last safe boundary rather than treating a startup or UI snapshot as a capability token.
- Generalize the proven migration-backup preview/quarantine approach into a path-confined, reference-aware managed-artifact service.
- Preserve data ownership, restart evidence, idempotency and auditability while keeping every real network gate off in tests and by default.
- Make the system and privacy pages consumers of backend truth, with stable machine codes and localized presentation.

**Non-Goals:**

- Selecting model providers or credentials, enabling OneBot/model/Qzone/history/outbound routes, or changing reply activation modes.
- Permanently deleting quarantine batches, automatically restoring files, or replacing the running SQLite database from the dashboard.
- Treating readiness as a general host antivirus, arbitrary process inspector or filesystem cleaner.
- Moving existing files during schema migration or scanning outside explicit project-managed roots.

## Decisions

### 1. Use typed probes and profile policies instead of one health boolean

Introduce domain contracts equivalent to:

- `ReadinessProfile`: `local_start`, `offline_shadow`, `controlled_real_effect`;
- `ProbeStatus`: `pass`, `warning`, `blocked`, `unknown`, `stale`;
- `ReadinessProbeResult`: stable code, scope, observed/expires times, source revision, safe detail and remediation code;
- `ReadinessDecision`: profile, bot QQ, process-instance ID, revision, decision time, blockers, warnings and probe results.

Application-level probe adapters cover database/backup, managed paths, administrator auth, launcher ports, configured workers, durable pauses, OneBot identity/connection, model capability gates, outbox/outbound state and activation scope. A profile policy selects which probe codes are required and how `unknown` is treated; required `unknown` and `stale` always block.

Alternative considered: merge existing status dictionaries in the frontend. Rejected because a dashboard snapshot cannot authorize startup or effects, has no consistent freshness semantics and would duplicate safety policy in TypeScript.

### 2. Split launcher preflight from in-process readiness

Checks that must happen before binding or lifecycle startup run in a small launcher preflight invoked by `__main__` before `uvicorn.run`: configuration validation, database read-only inspection, managed-root validation and administrator-port availability. It writes no database state and constructs no external client. Uvicorn development or test entry points may inject a signed in-memory launcher result; absence or instance mismatch is reported as `unknown`, never fabricated as passing.

After the process starts, `create_app` creates a random process-instance ID and the readiness service. In-process probes can recognize the current listener and current OneBot connection instead of interpreting the application's own bound port as a conflict. Worker startup remains separately gated; effect-capable workers do not start merely because `local_start` passes.

Alternative considered: probe every port inside FastAPI lifespan. Rejected because the administrator socket is already bound and cannot reliably distinguish self from an unrelated process without launcher evidence.

### 3. Persist sanitized snapshots, not authority tokens

Add SQLite records for readiness evaluations and probe results keyed by decision ID, process instance, bot, profile and monotonically increasing revision. Persist stable codes, timestamps, scope hashes and safe evidence only. Credentials, raw payloads, private bodies, absolute paths and confirmation secrets remain in memory or are never collected.

Persisted snapshots support audit and restart diagnosis but cannot authorize a new process. The current decision is recomputed from live/in-process providers. A short TTL applies to connection, port, worker and circuit evidence; immutable configuration fingerprints and database manifests use explicit revisions. Windows sleep is detected by absolute expiry, not by loop ticks.

Alternative considered: persist a `ready=true` flag. Rejected because it could survive restart, sleep or bot reconnection and incorrectly authorize effects.

### 4. Add a last-boundary readiness guard without replacing existing gates

Effect services receive a `ReadinessGuard` dependency and request an exact capability/scope decision immediately before the external adapter call. The guard first evaluates the relevant readiness profile and then existing subsystem policy continues to check its own activation revision, authorization, quota, pause and idempotency rules. A readiness pass is necessary but never sufficient.

The first implementation integrates guards at shared protected boundaries rather than every caller: protected model gateway, NapCat outbox transport, Qzone transport, history/profile adapters and owner-report handoff. This minimizes policy drift. Failures return stable blocker codes and create sanitized audit/report evidence; they do not retry by themselves.

Alternative considered: let each worker read a global readiness endpoint. Rejected because HTTP-to-self adds failure modes and workers could apply inconsistent interpretations.

### 5. Build one managed-artifact catalog over explicit adapters

Introduce artifact contracts with `artifact_id`, type, owner scope, project-relative path, bundle membership, size/digest, manifest type, created time, verification result, reference state, retention class and quarantine batch. Type adapters handle:

- `storage/backups/migrations` backup + manifest pairs;
- `storage/exports` privacy exports;
- `storage/privacy-backups` deletion backup and original-file ZIP bundles;
- referenced files under `storage/imports`;
- project-managed batches under `storage/trash`.

The catalog reconciles database metadata with read-only filesystem scans. It never recursively treats arbitrary storage files as managed artifacts. Paths are resolved from a server-owned root and checked for traversal and Windows symlink/junction/reparse-point escape before reading or moving. APIs exchange artifact IDs only.

Alternative considered: extend `DatabasePreflightService` with every file type. Rejected because database migration backup policy would become coupled to user-owned privacy and import lifecycles.

### 6. Keep retention policy separate from artifact integrity and references

An adapter verifies bytes and bundle semantics; a reference provider answers whether an artifact is protected by a live workflow; a retention policy classifies verified, unreferenced artifacts. This ordering is mandatory: integrity and references precede age/count policy.

Migration backups retain their accepted “newest three plus 90 days” rule. Other artifact classes initially expose their age and reference state but receive candidates only through explicitly configured policy values. No default policy silently turns pre-existing privacy artifacts into candidates. Permanent quarantine destruction remains outside this change.

Alternative considered: one global age threshold. Rejected because migration rollback files, privacy exports, deletion evidence and imported sources have different ownership and safety constraints.

### 7. Use revision-bound, single-use previews and recoverable batch states

A retention preview contains a random handle whose hash is stored with actor/session or owner identity, process-instance ID, policy revision, ordered artifact IDs, current digest/reference revisions, byte total, expiry and target batch type. Confirmation uses compare-and-set and immediately marks the handle consumed.

Quarantine batches use explicit states: `prepared`, `moving`, `quarantined`, `rollback_required`, `rolled_back`, `blocked`. Each bundle is moved within one managed volume where possible. Database state advances after filesystem evidence is checked. On a partial failure, moved files are returned in reverse order; if restart interrupts the operation, reconciliation compares source, destination and expected digest without deleting either copy.

Alternative considered: retain only an in-memory HMAC token like the existing migration cleanup. Rejected for the generalized workflow because restart recovery, actor binding, one-time use and durable batch reconciliation need persisted state. The existing endpoint can be adapted through the new service without invalidating its safe behavior.

### 8. Treat dashboard and owner reports as sanitized projections

Add authenticated APIs for profile evaluation, current detail, artifact inventory, verification refresh, retention preview, confirmation and quarantine history. DTOs use codes, relative display names, counts, bytes and user/system scopes. They never return raw manifests, arbitrary paths or confirmation hashes.

The application shell consumes a concise readiness projection; system settings shows profiles and evidence; privacy audit shows user-bound artifacts and batches. Both call the same backend services. Readiness and retention anomalies may create dashboard-only durable owner reports using existing dedupe/correlation behavior; this change does not enable the report delivery worker.

Alternative considered: separate dashboard-only cleanup logic. Rejected because it would split authorization and audit from owner-command or future automation surfaces.

### 9. Make no-network acceptance a composition-root invariant

Tests instantiate the real readiness, retention, API and lifecycle composition with temporary SQLite/files, a controllable UTC clock, deterministic process IDs, synthetic launcher results, fake port/OneBot/worker providers and adapters that fail if real network construction is attempted. Filesystem tests include Windows-style case normalization and simulated reparse/path escape behavior where the platform permits it.

External clients remain independently disabled. Acceptance tests assert that evaluating any profile, scanning artifacts, previewing cleanup, quarantining or emitting a dashboard-only report performs no QQ, model, Qzone, history, search or Internet call.

## Data Ownership and Trust Boundaries

- The immutable creator remains YCH（维护者）; readiness evaluates the configured operational owner and exact bot without changing identity policy.
- System artifacts use system scope; privacy and import artifacts retain an explicit user QQ. A user-scoped query cannot enumerate another user's metadata.
- SQLite owns workflow references, preview revisions and batch states. The filesystem owns bytes but is never trusted without current verification.
- `.env` supplies configuration ceilings only. Launcher evidence, OneBot/provider state and dashboard input are untrusted until validated by their adapters.
- The dashboard is an authenticated projection and command surface, not an authority source. Confirmation handles are actor-, instance-, revision- and expiry-bound.

## Idempotency and Concurrency

- A readiness evaluation may be repeated; each decision is immutable and only the latest matching process/profile revision is current.
- Probe refreshes use bounded concurrency and per-probe timeouts. One failed probe cannot erase other evidence.
- Artifact discovery upserts by stable type/scope/relative-path identity and digest revision; it never duplicates a bundle because a scan reruns.
- Preview confirmation is compare-and-set and single-use. Concurrent confirmations yield at most one `moving` batch.
- Batch reconciliation is safe to rerun after crash. It never deletes duplicate source/destination evidence to “fix” ambiguity automatically.

## Risks / Trade-offs

- **[Risk] Probe aggregation becomes a second copy of subsystem policy** → Probes report facts and stable codes; existing services retain their domain gates, and the boundary guard only adds a necessary profile decision.
- **[Risk] Full hashing of large archives makes the dashboard slow** → Cache verified digest revisions by size/mtime/file identity for display, but always perform current verification before a move; refresh runs off the request loop with bounded concurrency.
- **[Risk] Windows file locks prevent an otherwise valid quarantine** → Fail the batch, roll back moved members and show the locking artifact as blocked; never fall back to copy-and-delete silently.
- **[Risk] Reparse-point detection differs across platforms** → Centralize path validation, use Windows-aware tests, and fail closed when link metadata cannot be established.
- **[Risk] Too many blockers make readiness hard to read** → Stable codes are grouped by profile and severity with one remediation hint; no aggregate percentage hides a critical blocker.
- **[Trade-off] No automatic permanent purge means storage can continue growing** → Capacity and candidates become visible now; irreversible destruction remains an explicit later policy decision.

## Migration Plan

1. Add typed contracts and an additive schema migration for readiness snapshots/probes, managed artifacts, reference revisions, retention previews, quarantine batches/items and related audit correlation. Run existing verified pre-migration backup first.
2. Implement repository compare-and-set operations and temporary-database tests. Seed no passing readiness, no cleanup candidates and no effect activation.
3. Add read-only launcher/in-process probe providers and the profile evaluator. Keep all real-effect guards in report-only mode until tests establish parity with existing gates.
4. Add artifact adapters and read-only reconciliation for existing roots. Confirm that first scan produces no file mutation.
5. Add preview, quarantine, rollback and restart reconciliation behind authenticated APIs; adapt existing migration-backup cleanup through the generalized service.
6. Enforce readiness guards at shared external boundaries with every configuration ceiling still off, then add system/privacy dashboard projections and dashboard-only owner reports.
7. Run migration, restart/sleep, port conflict, path escape, file lock, stale preview, concurrency and no-network acceptance; update `.env.example`, readiness runbook and status documents.

Rollback is code-first: activate persistent pauses, stop workers, stop the application, restore the prior code and retain additive tables plus quarantine directories for investigation. Existing artifacts remain in their original paths unless a confirmed quarantine completed. If schema restoration is unavoidable, restore the verified pre-migration backup while NapCat, the API and every worker are stopped; never down-migrate or overwrite the running database from the dashboard.

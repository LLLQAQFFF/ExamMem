# ADR — append-only Grade Review events

## Decision

Add migration `0007_grade_reviews` after the frozen `0006` head. Migrations
`0001`–`0006` remain byte-identical to the source baseline.

Checkpoint, Trace, L1/L2, Decision Journal, Change Log and L3 can derive exam
review and operational issues, but they cannot truthfully derive a learner's
new assertion that a Grade is disputed or an administrator's later disposition.
Grade Review is explicitly separate from Learning Memory Correction, so storing
the assertion as a correction event would corrupt domain meaning.

The new table is an append-only event stream. It stores stable links to the
authenticated Scope and an existing checkpoint; it does not copy L1/L2/audit
facts and has no cross-database foreign key.

## Migration and operations

- Backfill: none; existing grades simply have no review event.
- Compatibility: old code ignores the new table; new code still reads all old
  checkpoints, including pre-artifact payloads.
- Constraints: primary key, per-user idempotency, action enum, JSON object and
  append-only trigger are installed in one transaction.
- Data impact: one small row per dispute/disposition; no rewrite of historical
  Grade, Learning Event or Memory rows.
- Rollback: downgrade is refused while rows exist. Operational rollback is to
  disable the plugin/API and retain the audit rows; removal requires an explicit
  archival decision and is intentionally not automated.
- Sensitive data: free-text rationale is authenticated user data and remains in
  ExamMem's independent PostgreSQL under the same access boundary as checkpoints.

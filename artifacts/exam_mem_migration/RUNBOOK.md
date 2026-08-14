# ExamMem first-party plugin Runbook

This Runbook operates only the migrated current Practice/Learning Memory loop.
It does not authorize production data changes, destructive downgrade, release
or deployment.

## 1. Boundaries and prerequisites

- Use a dedicated ExamMem PostgreSQL database with the `vector` extension.
- Never point `EXAM_MEM_DATABASE_URL` at a DeepTutor internal/shared database.
- The URL must use `postgresql+asyncpg` and include username, password, host and
  database. Keep it in the process secret environment, never a settings file.
- Confirm the target is an isolated test database or the intended local/
  production ExamMem database before migration or a write-path smoke test.
- Migrations `0001`–`0006` are immutable. New schema changes append revisions.
- Do not UPDATE/DELETE append-only L1, Trace, lifecycle audit, Change Log,
  baseline facts or Grade Review rows.

Secret-safe connection check:

```bash
python -c "from exam_mem.storage import load_database_settings; print(load_database_settings().safe_summary())"
```

## 2. Enable, disable and configure

Plugin enablement is read before plugin import from:

```text
<DEEPTUTOR_HOME>/data/user/settings/plugins.json
```

Disable without importing ExamMem:

```json
{"version": 1, "disabled": ["exam_mem"]}
```

An enabled plugin loads non-secret settings from:

```text
<DEEPTUTOR_HOME>/data/user/settings/plugin_exam_mem.json
```

Default effective settings are `enabled=true`, subject
`postgraduate_math_1`, `memory_backend=lifecycle`, and
`capabilities.exam_practice=true`. The Configuration page/API saves settings;
the running process keeps its Effective settings until restart. Every new exam
pins Effective mode, configuration revision and deterministic side effects.
Existing exams continue with their Pinned snapshot after a configuration
change.

Backend effects:

| Mode | Expected durable effects |
| --- | --- |
| `none` | checkpoint, Practice Trace |
| `native` | checkpoint, Practice Trace, Host Native Memory adapter |
| `append_only` | checkpoint, Practice Trace, L1 event |
| `vector` | checkpoint, Practice Trace, L1 event, vector baseline fact |
| `lifecycle` | checkpoint, Practice Trace, L1, L2, provenance, Decision Journal, Change Log, rebuilt L3 |

Changing mode never enables fallback. A missing required dependency is an
operator-visible failure.

## 3. Migrate and verify

Read-only code/head checks:

```bash
python -m alembic -c alembic.ini heads
python -m alembic -c alembic.ini history
```

Expected single code head: `0010_learning_observations`.

Apply to the already-confirmed ExamMem database:

```bash
python -m alembic -c alembic.ini upgrade head
python -m alembic -c alembic.ini current
```

Expected current head: `0010_learning_observations`. A fresh database has 22 public
tables including `alembic_version` and ten append-only triggers:

```text
tr_learning_events_append_only
tr_lifecycle_decisions_append_only
tr_memory_change_log_append_only
tr_baseline_memory_facts_append_only
tr_practice_trace_spans_append_only
tr_grade_review_events_append_only
tr_study_plan_versions_append_only
tr_assessment_versions_append_only
tr_learning_observations_append_only
tr_learning_observation_actions_append_only
```

There is no automated downgrade of `0007` through `0010` while protected product
rows exist. Disable
the plugin/API and retain audit data until an explicit archival decision is
approved.

## 4. Start and inspect

Start DeepTutor through the repository's normal local/production launcher after
exporting the DSN. Useful authenticated read probes:

```text
GET /api/v1/plugins/list
GET /api/v1/plugins/health
GET /api/v1/exam-mem/practice/sessions
GET /api/v1/exam-mem/issues
GET /api/v1/exam-mem/configuration
```

`/api/v1/plugins/list` must report plugin `exam_mem`, capability
`exam_practice`, migration head `0010_learning_observations`, and the single
`Smart Exam Prep` navigation entry. Learning Paths, Practice, Learning Memory,
Review and Configuration remain available as internal workspaces under that
entry; the old Issues deep link remains compatible while its UI is embedded in
Learning Memory.
`/api/v1/plugins/health` proves plugin lifecycle assembly only; ExamMem
does not currently register an active database health hook. Verify PostgreSQL
with `alembic current` and an authenticated read endpoint as separate checks.

Browser routes:

```text
/exam-mem/practice
/exam-mem/learning
/exam-mem/review
/exam-mem/memories
/exam-mem/issues
/exam-mem/configuration
```

Learning Paths reuses Host Mastery Path progress and tutoring. Practice may
select the current controlled exam Scope and an objective, then ask the native
Quiz capability through the neutral Host Turn contract to generate 2–10
questions. Optional sources are limited to PDF, TXT and Markdown for that one
generation request. ExamMem does not persist source content: it pins the
generated catalog, canonical Taxonomy ID, filename/MIME/SHA-256 provenance,
answers and rubric in the existing server-side checkpoint. Native Quiz's own
correctness result is never imported as Learning Memory evidence.

Repeated attempts keep the same exam/subject IDs and receive distinct Practice
session/Trace identities. The history API returns a derived `attempt_number`;
it is not a mutable database counter.

## 5. Recovery, correction and review

- Response loss: retry the exact answer with the same idempotency key. Browser
  `sessionStorage` preserves the pending request for the current tab.
- Lost browser state or later return: use Practice history and server-side
  Resume. Resume reads the latest scoped checkpoint, original Trace/context and
  Pinned runtime, then creates a new Host transport session.
- Failed workflow: inspect Review Trace and Issues. Retry only errors marked
  retryable; grader contract/version mismatch fails closed.
- Pending projection: the Issue remains open until checkpoint recovery refreshes
  L3. Never manufacture L3 rows or infer truth from an old projection.
- Incorrect Learning Memory: submit a confirmed correction. It appends L1 and
  lifecycle evidence; it does not edit or delete the old memory.
- Grade disagreement: submit a Grade Review dispute. An administrator may
  Uphold, or call the disposition API with a complete structured replacement
  Grade to Overturn. A Grade Review never mutates Learning Memory by itself.
- Plan cancellation requires explicit confirmation and remains a lifecycle
  transition, not a direct row edit.

All product reads and writes bind `user_id` from Host authentication. Clients
may select only exam/subject parameters exposed by the endpoint; cross-user
Scope access is not accepted.

## 6. Verification commands

Use the existing project environment; do not install or upgrade dependencies as
part of an incident check.

```bash
python -m ruff check deeptutor exam_mem deeptutor_plugins tests
python -m pytest -q
python -m pytest -q -m backend_mode tests/exam_mem

cd web
npm run lint
npm run test:node
npm run build
```

PostgreSQL integration tests require `EXAM_MEM_DATABASE_URL`; without it they
skip. Test fixtures use random schemas/rollback and must leave no random schema
or public business row. A production build may rewrite `web/next-env.d.ts` for
its output directory; do not commit that generated drift.

## 7. Incident stop conditions

Stop and obtain operator approval when the connection target is uncertain,
credentials must change, a destructive migration/downgrade is proposed, or a
shared/production database would be mutated. Do not work around a failure by
switching Backend, writing DeepTutor Native Memory directly, bypassing Scope,
editing append-only records, or replaying with a new idempotency identity.

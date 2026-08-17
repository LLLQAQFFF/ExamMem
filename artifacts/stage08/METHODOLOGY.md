# ExamMem Stage 08 Evaluation Methodology

## Pipeline

`Dataset → Materializer → Backend rollout → Trace → Deterministic judges → Report`

The controlled dataset contains 120 multi-session trajectories across twelve scenario types. Each scenario has ten distinct knowledge-point tasks rather than name-only template clones. The frozen split is seed `20260806`: dev 40 and test 80.

TutorBench informed the use of fixed learner profiles, multi-turn tasks and multidimensional reporting. ExamMem does not reuse TutorBench's conversational outcome score as a memory-correctness score: deterministic Gold lifecycle operations and states remain primary.

## Layer isolation

- Extraction metrics are N/A because rollout input is already a structured `LearningEvent`.
- Slot metrics call the production rule-based taxonomy normalizer independently.
- Backend candidates use a Gold-normalized registered slot; candidate values are derived from event facts, never from Gold lifecycle result values.
- Lifecycle, state, isolation and retrieval judges consume only observed Trace/snapshot data.
- Recommendation targets reuse the production `RecommendationPolicyV1`; only Lifecycle supplies typed StudentModel/L2 evidence, matching the current product path.
- Native typed lifecycle/state metrics are N/A because DeepTutor Native Memory exposes L1 JSONL and L2/L3 Markdown, not ExamMem lifecycle identities.

## Baselines

- `none`: discards all events and memories.
- `native`: actual DeepTutor L1/L2/L3 with one filesystem root per case.
- `append_only`: actual PostgreSQL event and baseline fact repositories.
- `vector`: append-only facts plus frozen local 1024-dimensional `feature_hash_embedding_v1` retrieval. This is a deterministic baseline, not a production embedding-quality claim.
- `lifecycle`: actual PostgreSQL repositories, Lifecycle Policy v1, Decision Journal, Change Log and post-commit L3 rebuild.

## Accuracy safeguards

- Every metric must be measured, undefined with a zero-denominator reason, or not applicable with a semantic reason.
- A five-arm report rejects different fairness hashes or case counts.
- Timeout and failures remain in Trace and bad-case artifacts.
- Test rollout is blocked in code; only schema/count/hash verification is allowed.
- Provider usage unavailable through the Host interface is not silently estimated.

## Known comparability limits

Native consolidation owns its internal prompt and temperature settings. The common fairness hash freezes dataset, configured model identity, top-k, timeout and retry policy, but cannot make Native Markdown consolidation and Lifecycle relation classification the same task. This is a structural baseline difference and must remain in report warnings.

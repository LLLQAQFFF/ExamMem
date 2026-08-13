"""Deterministic Stage 06 relation-classifier test doubles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from exam_mem.contracts import MemoryUpdateCandidate
from exam_mem.lifecycle import (
    LifecycleCandidateSnapshot,
    RelationClassifierOutput,
    ResolvedRelationClassification,
    resolve_validated_relation_output,
)


class FakeRelationClassifier:
    """Return a controlled strict output selected only by the candidate event ID."""

    def __init__(
        self,
        outputs_by_event_id: Mapping[str, RelationClassifierOutput | Mapping[str, object]],
    ) -> None:
        self._outputs = dict(outputs_by_event_id)
        self.calls: list[str] = []

    async def classify(
        self,
        candidate: MemoryUpdateCandidate,
        candidate_snapshots: Sequence[LifecycleCandidateSnapshot],
    ) -> ResolvedRelationClassification:
        self.calls.append(candidate.event_id)
        try:
            raw_output = self._outputs[candidate.event_id]
        except KeyError as exc:
            raise KeyError(f"no fake relation output for event {candidate.event_id!r}") from exc
        classification = RelationClassifierOutput.model_validate(raw_output)
        return resolve_validated_relation_output(
            candidate,
            candidate_snapshots,
            classification,
        )


__all__ = ["FakeRelationClassifier"]

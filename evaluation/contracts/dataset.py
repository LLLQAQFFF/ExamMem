"""Versioned contracts for the formal Stage 08 controlled dataset."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from evaluation.contracts.case import (
    PROTOCOL_SEED,
    PROTOCOL_VERSION,
    DatasetSplit,
    NonEmptyString,
    ScenarioType,
)
from evaluation.contracts.rollout import Sha256Digest
from exam_mem.contracts import ErrorType

DatasetVersion = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^exam_mem_controlled_v[0-9]+$"),
]


class StrictDatasetModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ControlledAnswer(StrictDatasetModel):
    """One auditable answer form used to materialize a learning event."""

    answer_id: NonEmptyString
    text_zh: NonEmptyString
    correct: bool
    error_type: ErrorType | None = None
    error_detail: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_audit_fields(self) -> ControlledAnswer:
        if self.correct and (self.error_type is not None or self.error_detail is not None):
            raise ValueError("a correct answer must not declare error audit fields")
        if not self.correct and (self.error_type is None or self.error_detail is None):
            raise ValueError("a wrong answer requires error_type and error_detail")
        return self


class ControlledQuestion(StrictDatasetModel):
    """A small evaluation-only question with a visible answer and rubric."""

    question_id: NonEmptyString
    knowledge_point_id: NonEmptyString
    subject_area: Literal["linear_algebra", "probability_theory"]
    difficulty: Annotated[float, Field(ge=0.0, le=1.0)]
    prompt_zh: NonEmptyString
    reference_answer_zh: NonEmptyString
    rubric_items: Annotated[list[NonEmptyString], Field(min_length=1)]
    answer_forms: Annotated[list[ControlledAnswer], Field(min_length=2)]

    @model_validator(mode="after")
    def validate_answer_forms(self) -> ControlledQuestion:
        answer_ids = [answer.answer_id for answer in self.answer_forms]
        if len(answer_ids) != len(set(answer_ids)):
            raise ValueError("answer_id must be unique within a question")
        if not any(answer.correct for answer in self.answer_forms):
            raise ValueError("a controlled question requires a correct answer")
        if not any(not answer.correct for answer in self.answer_forms):
            raise ValueError("a controlled question requires a wrong answer")
        return self


class LearnerProfile(StrictDatasetModel):
    """TutorBench-inspired first-person context, fixed before rollout."""

    profile_id: NonEmptyString
    background_zh: NonEmptyString
    learning_goal_zh: NonEmptyString
    known_well_zh: list[NonEmptyString]
    partial_knowledge_zh: list[NonEmptyString]
    beliefs_zh: Annotated[list[NonEmptyString], Field(min_length=1)]


class BenchmarkEntry(StrictDatasetModel):
    """Sidecar entry that gives one memory trajectory a realistic learner task."""

    case_id: NonEmptyString
    profile: LearnerProfile
    task_title_zh: NonEmptyString
    initial_message_zh: NonEmptyString
    target_knowledge_point_ids: Annotated[list[NonEmptyString], Field(min_length=1)]
    success_criteria_zh: Annotated[list[NonEmptyString], Field(min_length=1)]
    trajectory_family: NonEmptyString
    answer_by_event_id: dict[NonEmptyString, NonEmptyString] = Field(default_factory=dict)


class DatasetFileRecord(StrictDatasetModel):
    path: NonEmptyString
    split: DatasetSplit
    case_id: NonEmptyString
    scenario_type: ScenarioType
    sha256: Sha256Digest


class SplitManifest(StrictDatasetModel):
    split: DatasetSplit
    case_count: Annotated[int, Field(ge=1)]
    aggregate_sha256: Sha256Digest
    files: Annotated[list[DatasetFileRecord], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_files(self) -> SplitManifest:
        if self.split is DatasetSplit.PROTOCOL_CHECK:
            raise ValueError("the formal manifest may only contain dev and test")
        if len(self.files) != self.case_count:
            raise ValueError("case_count must equal the number of file records")
        if any(record.split is not self.split for record in self.files):
            raise ValueError("every file record must match its split manifest")
        paths = [record.path for record in self.files]
        case_ids = [record.case_id for record in self.files]
        if len(paths) != len(set(paths)) or len(case_ids) != len(set(case_ids)):
            raise ValueError("manifest paths and case_ids must be unique within a split")
        return self


class DatasetManifest(StrictDatasetModel):
    dataset_version: DatasetVersion
    protocol_version: Literal[PROTOCOL_VERSION]
    seed: Literal[PROTOCOL_SEED]
    generated_at: AwareDatetime
    question_bank_sha256: Sha256Digest
    benchmark_entries_sha256: Sha256Digest
    splits: Annotated[list[SplitManifest], Field(min_length=2, max_length=2)]
    frozen_test_sha256: Sha256Digest
    construction_notes: Annotated[list[NonEmptyString], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_formal_splits(self) -> DatasetManifest:
        splits = {split.split: split for split in self.splits}
        if set(splits) != {DatasetSplit.DEV, DatasetSplit.TEST}:
            raise ValueError("manifest must contain exactly one dev and one test split")
        expected_counts = {DatasetSplit.DEV: 40, DatasetSplit.TEST: 80}
        for split, expected_count in expected_counts.items():
            if splits[split].case_count != expected_count:
                raise ValueError(f"{split.value} manifest requires exactly {expected_count} cases")
        all_paths = [record.path for split in self.splits for record in split.files]
        all_case_ids = [record.case_id for split in self.splits for record in split.files]
        if len(all_paths) != len(set(all_paths)) or len(all_case_ids) != len(set(all_case_ids)):
            raise ValueError("manifest paths and case_ids must be unique across splits")
        if self.frozen_test_sha256 != splits[DatasetSplit.TEST].aggregate_sha256:
            raise ValueError("frozen_test_sha256 must match the test aggregate hash")
        return self


__all__ = [
    "BenchmarkEntry",
    "ControlledAnswer",
    "ControlledQuestion",
    "DatasetFileRecord",
    "DatasetManifest",
    "DatasetVersion",
    "LearnerProfile",
    "SplitManifest",
]

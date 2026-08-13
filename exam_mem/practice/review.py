"""Strict Grade Review contracts, separate from Learning Memory correction."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints, model_validator

from .contracts import GradeResult

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class GradeReviewAction(str, Enum):
    DISPUTE = "dispute"
    UPHOLD = "uphold"
    OVERTURN = "overturn"


class GradeReviewEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    review_event_id: NonEmptyString
    review_chain_id: NonEmptyString
    idempotency_key: NonEmptyString
    action: GradeReviewAction
    user_id: NonEmptyString
    exam_id: NonEmptyString
    subject_id: NonEmptyString
    practice_session_id: NonEmptyString
    checkpoint_key: NonEmptyString
    reason: NonEmptyString
    replacement_grade: GradeResult | None = None
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_action(self) -> GradeReviewEvent:
        if self.action is GradeReviewAction.OVERTURN and self.replacement_grade is None:
            raise ValueError("overturn requires replacement_grade")
        if self.action is not GradeReviewAction.OVERTURN and self.replacement_grade is not None:
            raise ValueError("only overturn accepts replacement_grade")
        return self


__all__ = ["GradeReviewAction", "GradeReviewEvent"]

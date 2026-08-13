"""Versioned normalization policy with an explicit calibration gate."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
import yaml

NormalizationPolicyName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]*$"),
]
Threshold = Annotated[float, Field(ge=0.0, le=1.0)]

_POLICY_DIR = Path(__file__).resolve().parent / "normalization_policies"
_SAFE_POLICY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class NormalizationPolicy(BaseModel):
    """Policy values that remain unusable until all dev-calibrated thresholds exist."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalization_policy: NormalizationPolicyName
    embedding_top_k: Annotated[int, Field(ge=2)]
    accept_threshold: Threshold | None
    review_threshold: Threshold | None
    top1_top2_margin: Threshold | None

    @model_validator(mode="after")
    def validate_calibration_state(self) -> NormalizationPolicy:
        thresholds = (
            self.accept_threshold,
            self.review_threshold,
            self.top1_top2_margin,
        )
        if any(value is None for value in thresholds) and not all(
            value is None for value in thresholds
        ):
            raise ValueError("normalization thresholds must be all calibrated or all null")
        if (
            self.accept_threshold is not None
            and self.review_threshold is not None
            and self.review_threshold > self.accept_threshold
        ):
            raise ValueError("review_threshold must not exceed accept_threshold")
        return self

    @property
    def is_calibrated(self) -> bool:
        return all(
            value is not None
            for value in (
                self.accept_threshold,
                self.review_threshold,
                self.top1_top2_margin,
            )
        )

    def require_calibrated(self) -> None:
        """Fail closed instead of treating placeholders as runtime thresholds."""
        if not self.is_calibrated:
            raise ValueError(
                f"normalization policy {self.normalization_policy!r} is not calibrated"
            )


def load_normalization_policy(policy_name: str) -> NormalizationPolicy:
    """Load one packaged policy without permitting path traversal."""
    if not _SAFE_POLICY_RE.fullmatch(policy_name):
        raise ValueError(f"invalid normalization policy name: {policy_name!r}")

    path = _POLICY_DIR / f"{policy_name}.yaml"
    if not path.is_file():
        raise ValueError(f"normalization policy does not exist: {policy_name}")

    payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"normalization policy payload must be an object: {policy_name}")
    policy = NormalizationPolicy.model_validate(payload)
    if policy.normalization_policy != policy_name:
        raise ValueError(
            f"normalization policy mismatch: requested {policy_name!r}, "
            f"file declares {policy.normalization_policy!r}"
        )
    return policy


__all__ = [
    "NormalizationPolicy",
    "NormalizationPolicyName",
    "load_normalization_policy",
]

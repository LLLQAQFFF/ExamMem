from __future__ import annotations

from pydantic import ValidationError
import pytest

from exam_mem.domain import NormalizationPolicy, load_normalization_policy


def _policy_payload() -> dict[str, object]:
    return {
        "normalization_policy": "test_policy_v1",
        "embedding_top_k": 5,
        "accept_threshold": None,
        "review_threshold": None,
        "top1_top2_margin": None,
    }


@pytest.mark.taxonomy
def test_packaged_policy_keeps_uncalibrated_thresholds_null() -> None:
    policy = load_normalization_policy("slot_normalizer_v1")

    assert policy.normalization_policy == "slot_normalizer_v1"
    assert policy.embedding_top_k == 5
    assert policy.accept_threshold is None
    assert policy.review_threshold is None
    assert policy.top1_top2_margin is None
    assert policy.is_calibrated is False


@pytest.mark.taxonomy
def test_uncalibrated_policy_fails_closed() -> None:
    policy = load_normalization_policy("slot_normalizer_v1")

    with pytest.raises(ValueError, match="is not calibrated"):
        policy.require_calibrated()


@pytest.mark.taxonomy
def test_policy_rejects_partially_filled_thresholds() -> None:
    payload = _policy_payload()
    payload["accept_threshold"] = 0.9

    with pytest.raises(ValidationError, match="all calibrated or all null"):
        NormalizationPolicy.model_validate(payload)


@pytest.mark.taxonomy
def test_policy_rejects_invalid_threshold_order() -> None:
    payload = _policy_payload()
    payload.update(
        {
            "accept_threshold": 0.7,
            "review_threshold": 0.8,
            "top1_top2_margin": 0.1,
        }
    )

    with pytest.raises(ValidationError, match="must not exceed"):
        NormalizationPolicy.model_validate(payload)


@pytest.mark.taxonomy
def test_policy_rejects_out_of_range_thresholds() -> None:
    payload = _policy_payload()
    payload.update(
        {
            "accept_threshold": 1.1,
            "review_threshold": 0.8,
            "top1_top2_margin": 0.1,
        }
    )

    with pytest.raises(ValidationError):
        NormalizationPolicy.model_validate(payload)


@pytest.mark.taxonomy
def test_policy_loader_rejects_unknown_or_unsafe_names() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        load_normalization_policy("missing_policy_v1")
    with pytest.raises(ValueError, match="invalid normalization policy name"):
        load_normalization_policy("../slot_normalizer_v1")

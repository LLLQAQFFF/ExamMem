from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import pytest

from evaluation.contracts.protocol import REQUIRED_METRIC_IDS, ProtocolConfig

PROTOCOL_PATH = (
    Path(__file__).resolve().parents[2] / "evaluation" / "protocols" / "evaluation_protocol_v1.json"
)


@pytest.fixture
def protocol_payload() -> dict[str, Any]:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


@pytest.mark.protocol
@pytest.mark.schema
def test_versioned_protocol_file_is_complete(protocol_payload: dict[str, Any]) -> None:
    protocol = ProtocolConfig.model_validate(protocol_payload)

    assert len(protocol.scenario_quotas) == 12
    assert sum(quota.protocol_check_count for quota in protocol.scenario_quotas) == 24
    assert {metric.metric_id for metric in protocol.metrics} == REQUIRED_METRIC_IDS


@pytest.mark.protocol
@pytest.mark.schema
def test_protocol_check_cases_never_contribute_to_formal_score(
    protocol_payload: dict[str, Any],
) -> None:
    protocol = ProtocolConfig.model_validate(protocol_payload)
    protocol_check = next(
        rule for rule in protocol.dataset_splits if rule.split == "protocol_check"
    )

    assert protocol_check.case_count == 24
    assert protocol_check.contributes_to_formal_score is False


@pytest.mark.protocol
@pytest.mark.schema
def test_protocol_rejects_a_missing_backend(protocol_payload: dict[str, Any]) -> None:
    payload = deepcopy(protocol_payload)
    payload["backend_modes"].remove("native")

    with pytest.raises(ValidationError, match="all five backend modes"):
        ProtocolConfig.model_validate(payload)


@pytest.mark.protocol
@pytest.mark.schema
def test_protocol_rejects_a_removed_unfavorable_metric(
    protocol_payload: dict[str, Any],
) -> None:
    payload = deepcopy(protocol_payload)
    payload["metrics"] = [
        metric
        for metric in payload["metrics"]
        if metric["metric_id"] != "pollution.false_merge_rate"
    ]

    with pytest.raises(ValidationError, match="complete registered metric set"):
        ProtocolConfig.model_validate(payload)

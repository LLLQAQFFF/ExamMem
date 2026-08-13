from __future__ import annotations

import hashlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
VERSIONS = PROJECT_ROOT / "exam_mem" / "storage" / "migrations" / "versions"
FROZEN_SHA256 = {
    "0001_learning_memory_schema.py": (
        "3419102cd5189701701baee57307026ddc1dedc899ce55ede4f80315a1dff4df"
    ),
    "0002_append_only_records.py": (
        "cfc49ecb322da4f1ae514871a107ed2d317317c75a18ba840c34270b4a543db7"
    ),
    "0003_defer_superseded_by_fk.py": (
        "e764e26eea67dfd3b8477b9e04db3a602f0c3e5fe7ca42eaa15aecbe9fff1e6c"
    ),
    "0004_lifecycle_audit_contract.py": (
        "2b7de5b830952037b1d73fa652f4b52c0cb353d45127f412359e9ab73adc50df"
    ),
    "0005_practice_backend_facts.py": (
        "2f81ff02987dbdc6ee36e19995cc4289d455c0c42d1f8a0f2adc614f02ce9f37"
    ),
    "0006_practice_workflow.py": (
        "16c002f8ac8520658a23444ec18a64c84d215e5fa225d0f7aafbb14d0ec2b1b8"
    ),
}


def test_migrations_match_the_frozen_source_byte_for_byte() -> None:
    actual = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(VERSIONS.glob("[0-9][0-9][0-9][0-9]_*.py"))
        if path.name in FROZEN_SHA256
    }

    assert actual == FROZEN_SHA256

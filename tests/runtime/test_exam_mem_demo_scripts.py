from __future__ import annotations

from pathlib import Path
import subprocess

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO = PROJECT_ROOT / "scripts" / "exam_mem_demo"


def _read(name: str) -> str:
    return (DEMO / name).read_text(encoding="utf-8")


def test_demo_shell_scripts_have_valid_bash_syntax() -> None:
    scripts = [
        DEMO / "_common.sh",
        DEMO / "start-demo.sh",
        DEMO / "status-demo.sh",
        DEMO / "stop-demo.sh",
        DEMO / "reset-demo.sh",
    ]

    subprocess.run(["bash", "-n", *map(str, scripts)], check=True)


def test_demo_resources_are_local_explicit_and_separate_from_production() -> None:
    common = _read("_common.sh")
    compose = _read("compose.demo.yaml")
    reset = _read("reset-demo.sh")

    assert 'DEMO_CONTAINER_NAME="exammem-demo-postgres"' in common
    assert 'DEMO_VOLUME_NAME="exammem-demo-postgres-data"' in common
    assert 'DEMO_DB_NAME="exammem_demo"' in common
    assert 'DEMO_DB_PASSWORD="exammem-demo-only"' in common
    assert 'DEMO_DB_PORT="${EXAM_MEM_DEMO_POSTGRES_PORT:-55434}"' in common
    assert "127.0.0.1:${EXAM_MEM_DEMO_POSTGRES_PORT:-55434}:5432" in compose
    assert "exammem-demo-postgres-data" in compose
    assert "down --volumes --remove-orphans" in reset
    assert 'DELETE ${DEMO_DB_NAME}' in reset


def test_demo_start_defaults_to_dev_and_never_rewrites_workspace_settings() -> None:
    start = _read("start-demo.sh")

    assert 'launch_mode="dev"' in start
    assert "--production" in start
    assert "demo_assert_ports_free" in start
    assert "-m alembic -c alembic.ini upgrade head" in start
    assert "-m deeptutor_cli start" in start
    assert "--dev" in start
    assert "system.json" in start
    assert "save_plugins" not in start
    assert "plugin_exam_mem.json" not in start

"""Run ExamMem's packaged Alembic configuration from an installed distribution."""

from __future__ import annotations

from pathlib import Path
import sys

from alembic.config import CommandLine


def main() -> None:
    config_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    CommandLine(prog="python -m exam_mem.storage.migrations").main(
        argv=["-c", str(config_path), *sys.argv[1:]]
    )


if __name__ == "__main__":
    main()

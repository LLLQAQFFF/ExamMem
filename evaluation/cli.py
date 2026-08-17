"""Dependency-light command line entry point for protocol acceptance checks."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import json
import sys
from typing import Any

from evaluation.contracts.case import PROTOCOL_SEED, PROTOCOL_VERSION
from evaluation.data_builder import DATASET_VERSION, build_formal_dataset
from evaluation.evaluators.slot import evaluate_slot
from evaluation.protocols.validation import (
    load_protocol,
    replay_split,
    validate_dataset,
    validate_formal_dataset,
)

CommandHandler = Callable[[argparse.Namespace], dict[str, Any]]


def _protocol_validate(args: argparse.Namespace) -> dict[str, Any]:
    protocol = load_protocol(args.version)
    return {
        "status": "ok",
        "command": "protocol validate",
        "protocol_version": protocol.protocol_version,
        "scenario_count": len(protocol.scenario_quotas),
        "metric_count": len(protocol.metrics),
        "backend_count": len(protocol.backend_modes),
    }


def _dataset_validate(args: argparse.Namespace) -> dict[str, Any]:
    result = validate_dataset(args.split, protocol_version=args.version)
    return {"status": "ok", "command": "dataset validate", **result}


def _dataset_build(args: argparse.Namespace) -> dict[str, Any]:
    if args.seed != PROTOCOL_SEED:
        raise ValueError(f"formal dataset seed is frozen at {PROTOCOL_SEED}")
    manifest = build_formal_dataset()
    return {
        "status": "ok",
        "command": "dataset build",
        "dataset_version": manifest.dataset_version,
        "seed": manifest.seed,
        "splits": {
            split.split.value: {
                "case_count": split.case_count,
                "aggregate_sha256": split.aggregate_sha256,
            }
            for split in manifest.splits
        },
    }


def _dataset_verify(args: argparse.Namespace) -> dict[str, Any]:
    result = validate_formal_dataset(args.dataset_version)
    return {"status": "ok", "command": "dataset verify", **result}


def _gold_replay(args: argparse.Namespace) -> dict[str, Any]:
    result = replay_split(args.split, protocol_version=args.version)
    return {"status": "ok", "command": "gold replay", **result}


def _evaluate_slot(args: argparse.Namespace) -> dict[str, Any]:
    result = evaluate_slot(split=args.split, taxonomy_version=args.taxonomy)
    return {"status": "ok", "command": "evaluate-slot", **result}


def _add_version_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--version",
        default=PROTOCOL_VERSION,
        help=f"Protocol version (default: {PROTOCOL_VERSION}).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evaluation.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    protocol = commands.add_parser("protocol", help="Validate protocol config.")
    protocol_actions = protocol.add_subparsers(dest="action", required=True)
    protocol_validate = protocol_actions.add_parser("validate")
    _add_version_argument(protocol_validate)
    protocol_validate.set_defaults(handler=_protocol_validate)

    dataset = commands.add_parser("dataset", help="Validate versioned cases.")
    dataset_actions = dataset.add_subparsers(dest="action", required=True)
    dataset_validate = dataset_actions.add_parser("validate")
    dataset_validate.add_argument("--split", required=True)
    _add_version_argument(dataset_validate)
    dataset_validate.set_defaults(handler=_dataset_validate)

    dataset_build = dataset_actions.add_parser(
        "build", help="Deterministically materialize the formal controlled dataset."
    )
    dataset_build.add_argument("--seed", type=int, default=PROTOCOL_SEED)
    dataset_build.set_defaults(handler=_dataset_build)

    dataset_verify = dataset_actions.add_parser(
        "verify", help="Verify formal cases, sidecars, quotas, and frozen hashes."
    )
    dataset_verify.add_argument("--dataset-version", default=DATASET_VERSION)
    dataset_verify.add_argument(
        "--no-content-output",
        action="store_true",
        required=True,
        help="Required holdout guard: never emit frozen test case contents.",
    )
    dataset_verify.set_defaults(handler=_dataset_verify)

    gold = commands.add_parser("gold", help="Replay Gold lifecycle states.")
    gold_actions = gold.add_subparsers(dest="action", required=True)
    gold_replay = gold_actions.add_parser("replay")
    gold_replay.add_argument("--split", required=True)
    _add_version_argument(gold_replay)
    gold_replay.set_defaults(handler=_gold_replay)

    evaluate_slot_parser = commands.add_parser(
        "evaluate-slot",
        help="Evaluate stage-four slot normalization against protocol Gold.",
    )
    evaluate_slot_parser.add_argument("--split", required=True)
    evaluate_slot_parser.add_argument("--taxonomy", required=True)
    evaluate_slot_parser.set_defaults(handler=_evaluate_slot)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: CommandHandler = args.handler
    try:
        result = handler(args)
    except (OSError, ValueError) as exc:
        print(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

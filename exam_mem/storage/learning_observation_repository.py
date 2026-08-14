"""Append-only Agent observations kept outside formal Learning Memory."""

from __future__ import annotations

from typing import Any, Literal, Sequence

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import learning_observation_actions, learning_observations

ObservationChannel = Literal["chat", "learning_path"]
ObservationAction = Literal["confirm", "dismiss"]


class LearningObservationConflict(RuntimeError):
    pass


class PostgresLearningObservationRepository:
    """Persist Agent summaries without granting them Learning Memory write authority."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def append(
        self,
        *,
        observation_id: str,
        user_id: str,
        exam_id: str,
        subject_id: str,
        taxonomy_version: str,
        channel: ObservationChannel,
        source_session_id: str,
        source_turn_ids: Sequence[str],
        knowledge_point_ids: Sequence[str],
        summary: str,
        rationale: str,
        confidence: float,
        agent_contract_version: str,
        source_fingerprint: str,
    ) -> dict[str, Any]:
        values = {
            "observation_id": observation_id,
            "user_id": user_id,
            "exam_id": exam_id,
            "subject_id": subject_id,
            "taxonomy_version": taxonomy_version,
            "channel": channel,
            "source_session_id": source_session_id,
            "source_turn_ids": list(dict.fromkeys(source_turn_ids)),
            "knowledge_point_ids": list(dict.fromkeys(knowledge_point_ids)),
            "summary": summary,
            "rationale": rationale,
            "confidence": confidence,
            "agent_contract_version": agent_contract_version,
            "source_fingerprint": source_fingerprint,
        }
        inserted = (
            (
                await self._connection.execute(
                    insert(learning_observations)
                    .values(**values)
                    .on_conflict_do_nothing(
                        index_elements=[
                            learning_observations.c.user_id,
                            learning_observations.c.source_fingerprint,
                        ]
                    )
                    .returning(learning_observations)
                )
            )
            .mappings()
            .one_or_none()
        )
        if inserted is not None:
            return _observation_payload(inserted, status="pending")
        existing = (
            (
                await self._connection.execute(
                    select(learning_observations).where(
                        learning_observations.c.user_id == user_id,
                        learning_observations.c.source_fingerprint == source_fingerprint,
                    )
                )
            )
            .mappings()
            .one()
        )
        expected = {
            key: values[key]
            for key in (
                "exam_id",
                "subject_id",
                "taxonomy_version",
                "channel",
                "source_session_id",
                "source_turn_ids",
                "agent_contract_version",
            )
        }
        actual = {key: existing[key] for key in expected}
        if actual != expected:
            raise LearningObservationConflict("observation source identity conflicts")
        status = await self._status(existing["observation_id"], user_id)
        return _observation_payload(existing, status=status)

    async def append_action(
        self,
        *,
        action_id: str,
        observation_id: str,
        user_id: str,
        action: ObservationAction,
        idempotency_key: str,
    ) -> dict[str, Any]:
        observation = await self.get(observation_id=observation_id, user_id=user_id)
        if observation is None:
            raise LookupError("learning observation not found")
        row = (
            (
                await self._connection.execute(
                    insert(learning_observation_actions)
                    .values(
                        action_id=action_id,
                        observation_id=observation_id,
                        user_id=user_id,
                        action=action,
                        idempotency_key=idempotency_key,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            learning_observation_actions.c.user_id,
                            learning_observation_actions.c.idempotency_key,
                        ]
                    )
                    .returning(learning_observation_actions)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            row = (
                (
                    await self._connection.execute(
                        select(learning_observation_actions).where(
                            learning_observation_actions.c.user_id == user_id,
                            learning_observation_actions.c.idempotency_key == idempotency_key,
                        )
                    )
                )
                .mappings()
                .one()
            )
            if row["observation_id"] != observation_id or row["action"] != action:
                raise LearningObservationConflict("observation action identity conflicts")
        return {**observation, "status": "confirmed" if action == "confirm" else "dismissed"}

    async def get(self, *, observation_id: str, user_id: str) -> dict[str, Any] | None:
        row = (
            (
                await self._connection.execute(
                    select(learning_observations).where(
                        learning_observations.c.observation_id == observation_id,
                        learning_observations.c.user_id == user_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return _observation_payload(
            row,
            status=await self._status(observation_id, user_id),
        )

    async def list(
        self,
        *,
        user_id: str,
        exam_id: str,
        subject_id: str,
        taxonomy_version: str | None = None,
        channel: ObservationChannel | None = None,
        knowledge_point_ids: Sequence[str] = (),
        status: Literal["pending", "confirmed", "dismissed"] | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(learning_observations).where(
            learning_observations.c.user_id == user_id,
            learning_observations.c.exam_id == exam_id,
            learning_observations.c.subject_id == subject_id,
        )
        if taxonomy_version is not None:
            statement = statement.where(
                learning_observations.c.taxonomy_version == taxonomy_version
            )
        if channel is not None:
            statement = statement.where(learning_observations.c.channel == channel)
        rows = (
            (
                await self._connection.execute(
                    statement.order_by(
                        learning_observations.c.created_at.desc(),
                        learning_observations.c.observation_id.desc(),
                    )
                )
            )
            .mappings()
            .all()
        )
        required_ids = set(knowledge_point_ids)
        output: list[dict[str, Any]] = []
        for row in rows:
            if required_ids and not required_ids.intersection(row["knowledge_point_ids"]):
                continue
            current_status = await self._status(row["observation_id"], user_id)
            if status is not None and current_status != status:
                continue
            output.append(_observation_payload(row, status=current_status))
        return output

    async def _status(self, observation_id: str, user_id: str) -> str:
        action = await self._connection.scalar(
            select(learning_observation_actions.c.action)
            .where(
                learning_observation_actions.c.observation_id == observation_id,
                learning_observation_actions.c.user_id == user_id,
            )
            .order_by(
                learning_observation_actions.c.created_at.desc(),
                learning_observation_actions.c.action_id.desc(),
            )
            .limit(1)
        )
        return {"confirm": "confirmed", "dismiss": "dismissed"}.get(str(action), "pending")


def _observation_payload(row: Any, *, status: str) -> dict[str, Any]:
    return {
        "observation_id": row["observation_id"],
        "exam_id": row["exam_id"],
        "subject_id": row["subject_id"],
        "taxonomy_version": row["taxonomy_version"],
        "channel": row["channel"],
        "source_session_id": row["source_session_id"],
        "source_turn_ids": list(row["source_turn_ids"]),
        "knowledge_point_ids": list(row["knowledge_point_ids"]),
        "summary": row["summary"],
        "rationale": row["rationale"],
        "confidence": float(row["confidence"]),
        "agent_contract_version": row["agent_contract_version"],
        "source_fingerprint": row["source_fingerprint"],
        "status": status,
        "created_at": row["created_at"].isoformat(),
    }


__all__ = [
    "LearningObservationConflict",
    "ObservationAction",
    "ObservationChannel",
    "PostgresLearningObservationRepository",
]

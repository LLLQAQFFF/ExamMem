"""Small async client for ExamMem's authenticated textbook-learning API."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient, Response


class ExamMemSDKError(RuntimeError):
    """Raised when the ExamMem HTTP boundary rejects an SDK operation."""


class ExamMemTextbookLearningSDK:
    def __init__(self, client: AsyncClient, *, api_prefix: str = "/api/v1/exam-mem") -> None:
        self._client = client
        self._api_prefix = api_prefix.rstrip("/")

    async def bind_textbook(
        self,
        *,
        plan_id: str,
        plan_version: int,
        textbook_version_id: str,
        role: str,
        priority: int,
        status: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"{self._api_prefix}/study-plans/{plan_id}/versions/{plan_version}/textbooks",
            json={
                "textbook_version_id": textbook_version_id,
                "role": role,
                "priority": priority,
                "status": status,
                "idempotency_key": idempotency_key,
            },
        )
        return self._payload(response)["binding"]

    async def list_textbook_bindings(
        self, *, plan_id: str, plan_version: int
    ) -> list[dict[str, Any]]:
        response = await self._client.get(
            f"{self._api_prefix}/study-plans/{plan_id}/versions/{plan_version}/textbooks"
        )
        return list(self._payload(response)["bindings"])

    async def open_study_objective(
        self,
        *,
        plan_id: str,
        objective_id: str,
        plan_version: int | None = None,
        language: str = "zh",
        source_mode: str = "primary",
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"{self._api_prefix}/study-plans/{plan_id}/objectives/{objective_id}/open",
            json={
                "version": plan_version,
                "language": language,
                "source_mode": source_mode,
            },
        )
        return self._payload(response)

    @staticmethod
    def _payload(response: Response) -> dict[str, Any]:
        payload = response.json()
        if response.is_success:
            return payload
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            message = str(detail.get("message") or detail)
        else:
            message = str(detail or f"ExamMem request failed ({response.status_code})")
        raise ExamMemSDKError(message)


__all__ = ["ExamMemSDKError", "ExamMemTextbookLearningSDK"]

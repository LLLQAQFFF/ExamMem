"""First-party ExamMem plugin assembly."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from deeptutor.plugins import (
    BaseFullStackPlugin,
    MigrationContribution,
    NavigationContribution,
    PluginManifest,
    RouterContribution,
    SettingsContribution,
    load_plugin_settings,
)
from exam_mem.config import ExamMemSettings
from exam_mem.practice.capability import ExamPracticeCapability
from exam_mem.practice.provider import PracticeRuntimeProvider
from exam_mem.practice.tools import (
    AnswerGraderTool,
    ErrorAnalyzerTool,
    KnowledgeMapperTool,
    MemoryReaderTool,
    MemoryWriterTool,
    QuestionRetrieverTool,
    RecommendationTool,
)

from .api import build_router
from .native_adapter import DeepTutorNativeMemoryClient


def _normalize_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    return ExamMemSettings.model_validate(settings).model_dump(mode="json")


def _settings_contribution() -> SettingsContribution:
    return SettingsContribution(
        namespace="exam_mem",
        defaults=ExamMemSettings().model_dump(mode="json"),
        normalize=_normalize_settings,
    )


class ExamMemPlugin(BaseFullStackPlugin):
    def __init__(
        self,
        settings: ExamMemSettings | None = None,
        *,
        engine_factory: Callable[[str], AsyncEngine] = create_async_engine,
    ) -> None:
        self.settings = settings or ExamMemSettings()
        self._engine_factory = engine_factory
        self._runtime_provider = PracticeRuntimeProvider(
            settings=self.settings,
            engine_factory=self._engine_factory,
            native_memory_client_factory=DeepTutorNativeMemoryClient,
        )
        settings_contribution = _settings_contribution()
        self.manifest = PluginManifest(
            name="exam_mem",
            version="1.0.0",
            description="Smart exam preparation and audited Learning Memory",
            capability_factories=(self._build_practice_capability,),
            tool_factories=(
                QuestionRetrieverTool,
                AnswerGraderTool,
                KnowledgeMapperTool,
                ErrorAnalyzerTool,
                MemoryReaderTool,
                MemoryWriterTool,
                RecommendationTool,
            ),
            routers=(
                RouterContribution(
                    router=build_router(
                        self._runtime_provider,
                        settings_contribution=settings_contribution,
                        effective_settings=self.settings,
                    ),
                    prefix="/api/v1/exam-mem",
                    tags=("exam-mem",),
                    access="authenticated",
                ),
            ),
            navigation=(
                NavigationContribution(
                    href="/exam-mem/practice",
                    label="Smart Exam Prep",
                    icon="BrainCircuit",
                    section="primary",
                    order=45,
                ),
            ),
            settings=settings_contribution,
            migration=MigrationContribution(
                config_path="exam_mem/storage/alembic.ini",
                versions_path="exam_mem/storage/migrations/versions",
                expected_head="0012_study_plan_archival",
            ),
            metadata={
                "product_surface": "exam_practice",
                "business_store": "exam_mem_postgresql",
                "native_memory_is_business_truth": False,
            },
        )

    def _build_practice_capability(self) -> ExamPracticeCapability:
        return ExamPracticeCapability(runtime_factory=self._runtime_provider)


def get_plugin() -> ExamMemPlugin:
    contribution = _settings_contribution()
    return ExamMemPlugin(
        settings=ExamMemSettings.model_validate(load_plugin_settings(contribution))
    )


__all__ = ["ExamMemPlugin", "get_plugin"]

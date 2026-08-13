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
        self.manifest = PluginManifest(
            name="exam_mem",
            version="1.0.0",
            description="Exam practice and audited Learning Memory",
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
                    router=build_router(self._runtime_provider),
                    prefix="/api/v1/exam-mem",
                    tags=("exam-mem",),
                    access="authenticated",
                ),
            ),
            navigation=(
                NavigationContribution(
                    href="/exam-mem/practice",
                    label="Exam Practice",
                    icon="BookOpenCheck",
                    section="primary",
                    order=45,
                ),
                NavigationContribution(
                    href="/exam-mem/memories",
                    label="Learning Memory",
                    icon="BrainCircuit",
                    section="secondary",
                    order=45,
                ),
            ),
            settings=SettingsContribution(
                namespace="exam_mem",
                defaults=ExamMemSettings().model_dump(mode="json"),
                normalize=_normalize_settings,
            ),
            migration=MigrationContribution(
                config_path="alembic.ini",
                versions_path="exam_mem/storage/migrations/versions",
                expected_head="0006_practice_workflow",
            ),
            metadata={
                "product_surface": "exam_practice",
                "business_store": "exam_mem_postgresql",
                "native_memory_is_business_truth": False,
            },
        )

    def _build_practice_capability(self) -> ExamPracticeCapability:
        return ExamPracticeCapability(
            runtime_factory=self._runtime_provider
        )


def get_plugin() -> ExamMemPlugin:
    return ExamMemPlugin()


__all__ = ["ExamMemPlugin", "get_plugin"]

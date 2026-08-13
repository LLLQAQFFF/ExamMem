from __future__ import annotations

from deeptutor.plugins import PluginManager
from deeptutor.runtime.registry.capability_registry import CapabilityRegistry
from deeptutor.runtime.registry.tool_registry import ToolRegistry
from deeptutor_plugins.exam_mem import ExamMemPlugin


def _manager() -> PluginManager:
    return PluginManager(factories={"exam_mem": ExamMemPlugin})


def test_exam_mem_manifest_contributes_only_plugin_owned_surfaces() -> None:
    manager = _manager()

    assert [item.name for item in manager.capabilities()] == ["exam_practice"]
    assert manager.capabilities()[0].manifest.session_surface == "exam_practice"
    assert [item.name for item in manager.tools()] == [
        "question_retriever",
        "answer_grader",
        "knowledge_mapper",
        "error_analyzer",
        "memory_reader",
        "memory_writer",
        "recommendation",
    ]
    assert [(item.prefix, item.access) for item in manager.routers()] == [
        ("/api/v1/exam-mem", "authenticated")
    ]
    assert [item.href for item in manager.navigation()] == [
        "/exam-mem/practice",
        "/exam-mem/review",
        "/exam-mem/memories",
        "/exam-mem/issues",
        "/exam-mem/configuration",
    ]
    assert manager.settings()[0].namespace == "exam_mem"
    assert manager.plugins[0].manifest.migration is not None
    assert manager.plugins[0].manifest.migration.expected_head == "0007_grade_reviews"


def test_exam_mem_registers_through_neutral_host_registries(monkeypatch) -> None:
    manager = _manager()
    monkeypatch.setattr("deeptutor.plugins.get_plugin_manager", lambda: manager)
    capabilities = CapabilityRegistry()
    tools = ToolRegistry()
    tools.load_builtins()

    capabilities.load_plugins()
    tools.load_plugins()

    assert capabilities.list_capabilities() == ["exam_practice"]
    assert set(manager.describe()[0]["tools"]).issubset(tools.list_tools())


def test_disabled_exam_mem_is_not_materialized() -> None:
    called = False

    def factory() -> ExamMemPlugin:
        nonlocal called
        called = True
        return ExamMemPlugin()

    manager = PluginManager(factories={"exam_mem": factory}, disabled=("exam_mem",))

    assert manager.plugins == ()
    assert manager.navigation() == ()
    assert manager.routers() == ()
    assert manager.settings() == ()
    assert called is False

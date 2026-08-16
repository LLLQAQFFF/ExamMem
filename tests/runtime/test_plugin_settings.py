from __future__ import annotations

import json

from deeptutor.plugins import SettingsContribution, load_plugin_settings, save_plugin_settings
from deeptutor.services.path_service import PathService


def _normalize(value):  # noqa: ANN001, ANN202
    return {"enabled": bool(value.get("enabled", True)), "mode": str(value.get("mode", "a"))}


def test_plugin_settings_are_namespaced_normalized_and_atomic(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    service = PathService(workspace_root=tmp_path / "admin")
    monkeypatch.setattr("deeptutor.plugins.settings.get_admin_path_service", lambda: service)
    contribution = SettingsContribution(
        namespace="fake_plugin",
        defaults={"enabled": True, "mode": "a"},
        normalize=_normalize,
    )

    assert load_plugin_settings(contribution) == {"enabled": True, "mode": "a"}
    saved = save_plugin_settings(contribution, {"enabled": 0, "mode": "b"})

    path = service.get_settings_file("plugin_fake_plugin")
    assert saved == {"enabled": False, "mode": "b"}
    assert json.loads(path.read_text(encoding="utf-8")) == saved
    assert load_plugin_settings(contribution) == saved
    assert list(path.parent.glob("tmp*")) == []

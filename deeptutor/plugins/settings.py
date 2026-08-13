"""Durable, domain-neutral storage for plugin settings contributions."""

from __future__ import annotations

import json
from typing import Any

from deeptutor.multi_user.paths import get_admin_path_service
from deeptutor.services.file_io import atomic_write_json

from .contracts import SettingsContribution


def load_plugin_settings(contribution: SettingsContribution) -> dict[str, Any]:
    path = _settings_path(contribution)
    payload: dict[str, Any] = dict(contribution.defaults)
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("plugin settings file must contain one JSON object")
        payload.update(loaded)
    return contribution.normalize(payload)


def save_plugin_settings(
    contribution: SettingsContribution,
    payload: dict[str, Any],
) -> dict[str, Any]:
    normalized = contribution.normalize(payload)
    atomic_write_json(_settings_path(contribution), normalized)
    return normalized


def _settings_path(contribution: SettingsContribution):  # noqa: ANN202
    namespace = contribution.namespace.strip()
    if not namespace or not namespace.replace("_", "").isalnum():
        raise ValueError("plugin settings namespace must be alphanumeric with underscores")
    return get_admin_path_service().get_settings_file(f"plugin_{namespace}")


__all__ = ["load_plugin_settings", "save_plugin_settings"]

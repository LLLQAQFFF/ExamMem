"""FastAPI mounting helpers for domain-neutral plugin routers."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import FastAPI
from fastapi.params import Depends

from .manager import PluginManager


def mount_plugin_routers(
    app: FastAPI,
    manager: PluginManager,
    *,
    authenticated_dependencies: Sequence[Depends] = (),
    admin_dependencies: Sequence[Depends] = (),
) -> None:
    """Mount cached plugin routers with the access policy declared by each plugin."""

    dependencies = {
        "public": (),
        "authenticated": authenticated_dependencies,
        "admin": admin_dependencies,
    }
    for contribution in manager.routers():
        app.include_router(
            contribution.router,
            prefix=contribution.prefix,
            tags=list(contribution.tags),
            dependencies=list(dependencies[contribution.access]),
        )


__all__ = ["mount_plugin_routers"]

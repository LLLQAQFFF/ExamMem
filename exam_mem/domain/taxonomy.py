"""Versioned knowledge-point taxonomy contracts and deterministic validation."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from pathlib import Path
import re
from typing import Annotated, Any
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
import yaml

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CanonicalKnowledgePointId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)*$",
    ),
]
TaxonomyVersion = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^[a-z][a-z0-9_]*$"),
]

_TAXONOMY_DIR = Path(__file__).resolve().parent / "taxonomies"
_SAFE_VERSION_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class KnowledgePointStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class TaxonomyNode(BaseModel):
    """One stable node in a single-parent knowledge-point tree."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: CanonicalKnowledgePointId
    name_zh: NonEmptyString
    parent_id: CanonicalKnowledgePointId | None = None
    aliases: tuple[NonEmptyString, ...] = ()
    prerequisites: tuple[CanonicalKnowledgePointId, ...] = ()
    status: KnowledgePointStatus = KnowledgePointStatus.ACTIVE
    replaced_by: CanonicalKnowledgePointId | None = None

    @model_validator(mode="after")
    def validate_local_consistency(self) -> TaxonomyNode:
        if len(self.aliases) != len({_label_key(alias) for alias in self.aliases}):
            raise ValueError(f"node {self.id!r} contains duplicate aliases")
        if len(self.prerequisites) != len(set(self.prerequisites)):
            raise ValueError(f"node {self.id!r} contains duplicate prerequisites")
        if self.id in self.prerequisites:
            raise ValueError(f"node {self.id!r} cannot require itself")
        if self.status is KnowledgePointStatus.ACTIVE and self.replaced_by is not None:
            raise ValueError("an active node must not declare replaced_by")
        if self.status is KnowledgePointStatus.DEPRECATED and self.replaced_by is None:
            raise ValueError("a deprecated node must declare replaced_by")
        if self.replaced_by == self.id:
            raise ValueError("a deprecated node cannot replace itself")
        return self


class Taxonomy(BaseModel):
    """An immutable, versioned taxonomy validated before normalization uses it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    taxonomy_version: TaxonomyVersion
    nodes: tuple[TaxonomyNode, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_structure(self) -> Taxonomy:
        nodes_by_id: dict[str, TaxonomyNode] = {}
        for node in self.nodes:
            if node.id in nodes_by_id:
                raise ValueError(f"duplicate taxonomy node id: {node.id}")
            nodes_by_id[node.id] = node

        for node in self.nodes:
            if node.parent_id is not None and node.parent_id not in nodes_by_id:
                raise ValueError(f"node {node.id!r} references unknown parent {node.parent_id!r}")

        _validate_parent_cycles(nodes_by_id)

        roots = [node for node in self.nodes if node.parent_id is None]
        if len(roots) != 1:
            raise ValueError(f"taxonomy must contain exactly one root, found {len(roots)}")

        for node in self.nodes:
            if node.parent_id is None:
                continue
            expected_parent = node.id.rpartition(".")[0]
            if node.parent_id != expected_parent:
                raise ValueError(
                    f"node {node.id!r} parent must match its dotted path: "
                    f"expected {expected_parent!r}, got {node.parent_id!r}"
                )

        _validate_labels(self.nodes)

        for node in self.nodes:
            for prerequisite in node.prerequisites:
                if prerequisite not in nodes_by_id:
                    raise ValueError(
                        f"node {node.id!r} references unknown prerequisite {prerequisite!r}"
                    )
            if node.replaced_by is not None:
                replacement = nodes_by_id.get(node.replaced_by)
                if replacement is None:
                    raise ValueError(
                        f"node {node.id!r} references unknown replacement {node.replaced_by!r}"
                    )
                if replacement.status is not KnowledgePointStatus.ACTIVE:
                    raise ValueError(f"replacement for node {node.id!r} must be active")
        return self

    def get(self, knowledge_point_id: str) -> TaxonomyNode | None:
        return next((node for node in self.nodes if node.id == knowledge_point_id), None)

    def children_of(self, parent_id: str) -> tuple[TaxonomyNode, ...]:
        return tuple(node for node in self.nodes if node.parent_id == parent_id)


def _label_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _validate_labels(nodes: tuple[TaxonomyNode, ...]) -> None:
    owners: dict[str, str] = {}
    for node in nodes:
        for label in (node.name_zh, *node.aliases):
            key = _label_key(label)
            owner = owners.get(key)
            if owner is not None and owner != node.id:
                raise ValueError(
                    f"taxonomy label {label!r} conflicts between {owner!r} and {node.id!r}"
                )
            owners[key] = node.id


def _validate_parent_cycles(nodes_by_id: Mapping[str, TaxonomyNode]) -> None:
    for node_id in nodes_by_id:
        visited: set[str] = set()
        current_id: str | None = node_id
        while current_id is not None:
            if current_id in visited:
                raise ValueError(f"taxonomy parent cycle detected at {current_id!r}")
            visited.add(current_id)
            current_id = nodes_by_id[current_id].parent_id


def load_taxonomy(version: str) -> Taxonomy:
    """Load one packaged taxonomy version without permitting path traversal."""
    if not _SAFE_VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid taxonomy version: {version!r}")

    path = _TAXONOMY_DIR / f"{version}.yaml"
    if not path.is_file():
        raise ValueError(f"taxonomy version does not exist: {version}")

    payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"taxonomy payload must be an object: {version}")
    taxonomy = Taxonomy.model_validate(payload)
    if taxonomy.taxonomy_version != version:
        raise ValueError(
            f"taxonomy version mismatch: requested {version!r}, "
            f"file declares {taxonomy.taxonomy_version!r}"
        )
    return taxonomy


__all__ = [
    "CanonicalKnowledgePointId",
    "KnowledgePointStatus",
    "Taxonomy",
    "TaxonomyNode",
    "TaxonomyVersion",
    "load_taxonomy",
]

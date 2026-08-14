"""Strict contracts for imported, versioned exam study plans."""

from __future__ import annotations

from enum import Enum
import hashlib
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from exam_mem.domain import KnowledgePointStatus, Taxonomy, TaxonomyNode

NonEmptyString = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
CanonicalNodeId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_]*)*$",
    ),
]


class StudyObjectiveType(str, Enum):
    MEMORY = "memory"
    CONCEPT = "concept"
    PROCEDURE = "procedure"
    DESIGN = "design"


class StrictStudyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ImportedObjective(StrictStudyModel):
    name: NonEmptyString
    type: StudyObjectiveType = StudyObjectiveType.CONCEPT


class ImportedModule(StrictStudyModel):
    name: NonEmptyString
    knowledge_points: tuple[ImportedObjective, ...] = Field(min_length=1, max_length=200)


class ImportedSubject(StrictStudyModel):
    name: NonEmptyString
    modules: tuple[ImportedModule, ...] = Field(min_length=1, max_length=100)


class ImportedOutline(StrictStudyModel):
    name: NonEmptyString
    subjects: tuple[ImportedSubject, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_labels(self) -> ImportedOutline:
        total = sum(
            len(module.knowledge_points)
            for subject in self.subjects
            for module in subject.modules
        )
        if total > 2_000:
            raise ValueError("an imported outline may contain at most 2000 knowledge points")
        _require_unique("subject", [subject.name for subject in self.subjects])
        for subject in self.subjects:
            _require_unique("module", [module.name for module in subject.modules])
            for module in subject.modules:
                _require_unique(
                    "knowledge point",
                    [objective.name for objective in module.knowledge_points],
                )
        return self


class StudyObjective(StrictStudyModel):
    id: CanonicalNodeId
    name: NonEmptyString
    type: StudyObjectiveType
    order: Annotated[int, Field(ge=0)]


class StudyModule(StrictStudyModel):
    id: CanonicalNodeId
    name: NonEmptyString
    order: Annotated[int, Field(ge=0)]
    knowledge_points: tuple[StudyObjective, ...] = Field(min_length=1)


class StudySubject(StrictStudyModel):
    id: CanonicalNodeId
    name: NonEmptyString
    order: Annotated[int, Field(ge=0)]
    modules: tuple[StudyModule, ...] = Field(min_length=1)


class StudyPlanTree(StrictStudyModel):
    name: NonEmptyString
    subjects: tuple[StudySubject, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_structure(self) -> StudyPlanTree:
        ids: list[str] = []
        _require_unique("subject", [subject.name for subject in self.subjects])
        for subject in self.subjects:
            ids.append(subject.id)
            _require_dense_orders(
                f"modules in {subject.id}", [module.order for module in subject.modules]
            )
            for module in subject.modules:
                if module.id.rpartition(".")[0] != subject.id:
                    raise ValueError(f"module {module.id!r} is outside subject {subject.id!r}")
                ids.append(module.id)
                _require_dense_orders(
                    f"objectives in {module.id}",
                    [objective.order for objective in module.knowledge_points],
                )
                for objective in module.knowledge_points:
                    if objective.id.rpartition(".")[0] != module.id:
                        raise ValueError(
                            f"objective {objective.id!r} is outside module {module.id!r}"
                        )
                    ids.append(objective.id)
        _require_dense_orders("subjects", [subject.order for subject in self.subjects])
        if len(ids) != len(set(ids)):
            raise ValueError("study plan node IDs must be globally unique")
        return self

    def subject(self, subject_id: str) -> StudySubject | None:
        return next((subject for subject in self.subjects if subject.id == subject_id), None)

    def objective(self, objective_id: str) -> tuple[StudySubject, StudyModule, StudyObjective] | None:
        for subject in self.subjects:
            for module in subject.modules:
                for objective in module.knowledge_points:
                    if objective.id == objective_id:
                        return subject, module, objective
        return None

    def taxonomy(self, subject_id: str, taxonomy_version: str) -> Taxonomy:
        subject = self.subject(subject_id)
        if subject is None:
            raise ValueError(f"unknown study-plan subject: {subject_id}")
        nodes: list[TaxonomyNode] = [
            TaxonomyNode(id=subject.id, name_zh=subject.name),
        ]
        for module in subject.modules:
            nodes.append(
                TaxonomyNode(id=module.id, name_zh=module.name, parent_id=subject.id)
            )
            nodes.extend(
                TaxonomyNode(
                    id=objective.id,
                    name_zh=objective.name,
                    parent_id=module.id,
                    status=KnowledgePointStatus.ACTIVE,
                )
                for objective in module.knowledge_points
            )
        return Taxonomy(taxonomy_version=taxonomy_version, nodes=tuple(nodes))


def materialize_outline(plan_id: str, outline: ImportedOutline) -> StudyPlanTree:
    """Assign deterministic, label-independent canonical IDs to one draft outline."""
    root = f"p{hashlib.sha256(plan_id.encode()).hexdigest()[:12]}"
    subjects = []
    for subject_index, imported_subject in enumerate(outline.subjects):
        subject_id = f"{root}.s{subject_index + 1:03d}"
        modules = []
        for module_index, imported_module in enumerate(imported_subject.modules):
            module_id = f"{subject_id}.m{module_index + 1:03d}"
            objectives = tuple(
                StudyObjective(
                    id=f"{module_id}.k{objective_index + 1:03d}",
                    name=objective.name,
                    type=objective.type,
                    order=objective_index,
                )
                for objective_index, objective in enumerate(
                    imported_module.knowledge_points
                )
            )
            modules.append(
                StudyModule(
                    id=module_id,
                    name=imported_module.name,
                    order=module_index,
                    knowledge_points=objectives,
                )
            )
        subjects.append(
            StudySubject(
                id=subject_id,
                name=imported_subject.name,
                order=subject_index,
                modules=tuple(modules),
            )
        )
    return StudyPlanTree(name=outline.name, subjects=tuple(subjects))


def _require_unique(kind: str, labels: list[str]) -> None:
    normalized = [" ".join(label.split()).casefold() for label in labels]
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"duplicate {kind} labels are not allowed at the same level")


def _require_dense_orders(kind: str, orders: list[int]) -> None:
    if sorted(orders) != list(range(len(orders))):
        raise ValueError(f"{kind} must use dense zero-based order values")


__all__ = [
    "ImportedModule",
    "ImportedObjective",
    "ImportedOutline",
    "ImportedSubject",
    "StudyModule",
    "StudyObjective",
    "StudyObjectiveType",
    "StudyPlanTree",
    "StudySubject",
    "materialize_outline",
]

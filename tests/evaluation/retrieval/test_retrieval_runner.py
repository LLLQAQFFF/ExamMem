from evaluation.retrieval.retrieval_runner import _plan_uses_hnsw, _vector_literal


def test_plan_detection_requires_the_production_hnsw_index() -> None:
    hnsw = {
        "Plan": {
            "Node Type": "Index Scan",
            "Index Name": "ix_learning_memories_content_embedding_hnsw",
        }
    }
    btree = {
        "Plan": {
            "Node Type": "Index Scan",
            "Index Name": "ix_learning_memories_scope_slot_state",
        }
    }

    assert _plan_uses_hnsw(hnsw)
    assert not _plan_uses_hnsw(btree)
    assert not _plan_uses_hnsw({"Plan": {"Node Type": "Seq Scan"}})


def test_vector_literal_preserves_finite_1024_dimension_vector() -> None:
    literal = _vector_literal([0.25] * 1024)

    assert literal.startswith("[") and literal.endswith("]")
    assert len(literal.removeprefix("[").removesuffix("]").split(",")) == 1024

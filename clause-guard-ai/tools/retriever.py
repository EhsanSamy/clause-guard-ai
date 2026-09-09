from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from typing import Optional, Union

from llama_index.core import VectorStoreIndex
from llama_index.core.vector_stores.types import (
    FilterOperator,
    MetadataFilter,
    MetadataFilters,
)

from retrieval.build_index import build_index
from retrieval.index_config import CONFIG, IndexConfig
from schemas.clause_schema import ClauseTag

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedRuleResult:
    retrieved_rule: Optional[str]
    retrieved_rule_found: bool
    source_file: Optional[str] = None
    similarity_score: Optional[float] = None
    error: Optional[str] = None

    def to_clause_fields(self) -> dict:
        """Exactly the two fields schemas/clause_schema.py's Clause model
        expects, ready to spread into a partial update."""
        return {
            "retrieved_rule": self.retrieved_rule,
            "retrieved_rule_found": self.retrieved_rule_found,
        }


# --------------------------------------------------------------------------- #
# Index loading — cached so nodes/retrieve_playbook_rules.py doesn't rebuild
# or reload the index once per clause.
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def _cached_index(config: IndexConfig = CONFIG) -> VectorStoreIndex:
    return build_index(config=config)


def get_index(config: IndexConfig = CONFIG, force_reload: bool = False) -> VectorStoreIndex:
    if force_reload:
        _cached_index.cache_clear()
    return _cached_index(config)


def clear_index_cache() -> None:
    """Test helper — call between tests that use different index configs."""
    _cached_index.cache_clear()


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #

def retrieve_rule(
    clause_text: str,
    clause_tag: Union[ClauseTag, str],
    index: Optional[VectorStoreIndex] = None,
    top_k: int = 1,
) -> RetrievedRuleResult:
    tag_value = clause_tag.value if isinstance(clause_tag, ClauseTag) else str(clause_tag)

    if tag_value == ClauseTag.OTHER.value:
        return RetrievedRuleResult(
            retrieved_rule=None,
            retrieved_rule_found=False,
            source_file=None,
        )

    if not clause_text or not clause_text.strip():
        logger.warning("retrieve_rule called with blank clause_text for tag=%s", tag_value)
        return RetrievedRuleResult(
            retrieved_rule=None,
            retrieved_rule_found=False,
            error="blank clause_text passed to retriever",
        )

    try:
        active_index = index if index is not None else get_index()

        filters = MetadataFilters(
            filters=[
                MetadataFilter(key="clause_tag", value=tag_value, operator=FilterOperator.EQ)
            ]
        )
        retriever = active_index.as_retriever(similarity_top_k=top_k, filters=filters)
        results = retriever.retrieve(clause_text)

    except Exception as exc:
        logger.exception("retrieve_rule tool failure for tag=%s", tag_value)
        return RetrievedRuleResult(
            retrieved_rule=None,
            retrieved_rule_found=False,
            error=f"retriever tool failure for tag '{tag_value}': {exc}",
        )

    if not results:
        return RetrievedRuleResult(
            retrieved_rule=None,
            retrieved_rule_found=False,
            source_file=None,
        )

    top = results[0]
    return RetrievedRuleResult(
        retrieved_rule=top.node.get_content().strip(),
        retrieved_rule_found=True,
        source_file=top.node.metadata.get("file_name"),
        similarity_score=top.score,
    )


def retrieve_rules_batch(
    clauses: list,
    index: Optional[VectorStoreIndex] = None,
) -> list[RetrievedRuleResult]:
    active_index = index if index is not None else get_index()
    return [
        retrieve_rule(c.clause_text, c.clause_tag, index=active_index) for c in clauses
    ]
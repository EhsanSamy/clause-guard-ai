"""
Supported embedding modes:

    1. openai
        Real OpenAI embeddings through the OpenAI API.

    2. local
        Local Hugging Face / Sentence Transformers embeddings.
        No API key or internet call is required after the model is downloaded.

    3. mock
        Fake embeddings used only for tests and pipeline-shape validation.
        Mock embeddings are NOT semantically meaningful.

The embedding provider is selected using:

    EMBEDDING_PROVIDER=openai
    EMBEDDING_PROVIDER=local
    EMBEDDING_PROVIDER=mock

RETRIEVAL_FORCE_MOCK=true always forces mock mode regardless of
EMBEDDING_PROVIDER.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from schemas.clause_schema import ClauseTag

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Supported embedding providers
# ---------------------------------------------------------------------------
EmbeddingProvider = Literal["openai", "local", "mock"]


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable safely."""
    val = os.getenv(name)

    if val is None:
        return default

    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_provider() -> EmbeddingProvider:
    if _env_bool("RETRIEVAL_FORCE_MOCK", False):
        return "mock"

    provider = os.getenv(
        "EMBEDDING_PROVIDER",
        "local",
    ).strip().lower()

    if provider not in {"openai", "local", "mock"}:
        raise ValueError(
            f"Unsupported EMBEDDING_PROVIDER={provider!r}. "
            "Expected one of: 'openai', 'local', 'mock'."
        )

    return provider

# ---------------------------------------------------------------------------
# Index configuration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IndexConfig:
    # Source / storage locations
    knowledge_base_dir: Path = field(
        default_factory=lambda: PROJECT_ROOT
        / os.getenv(
            "RETRIEVAL_KNOWLEDGE_BASE_DIR",
            "retrieval/knowledge_base",
        )
    )

    persist_dir: Path = field(
        default_factory=lambda: PROJECT_ROOT
        / os.getenv(
            "RETRIEVAL_STORAGE_DIR",
            "retrieval/storage",
        )
    )

    chunk_size: int = 512

    chunk_overlap: int = 64

    embedding_provider: EmbeddingProvider = field(
        default_factory=_env_provider
    )

    embedding_model: str = field(
        default_factory=lambda: os.getenv(
            "EMBEDDING_MODEL",
            "sentence-transformers/all-MiniLM-L6-v2",
        )
    )

    embedding_api_key: str | None = field(
        default_factory=lambda: (
            os.getenv("EMBEDDING_API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
    )

    mock_embedding_dim: int = 8

    top_k: int = field(
        default_factory=lambda: int(
            os.getenv("RETRIEVAL_TOP_K", "1")
        )
    )
    similarity_threshold: float = 0.0

    # =========================================================================
    # Knowledge-base validation
    # =========================================================================

    def expected_clause_tags(self) -> list[str]:
        """
        Return every ClauseTag that should have a playbook file.

        ClauseTag.OTHER is excluded intentionally because "other" means
        that there is no corresponding playbook coverage.
        """
        return [
            tag.value
            for tag in ClauseTag
            if tag != ClauseTag.OTHER
        ]

    def expected_file_for_tag(self, tag: str) -> Path:
        """Return the expected playbook file for a clause tag."""
        return self.knowledge_base_dir / f"{tag}.md"

    def validate_knowledge_base(self) -> list[str]:
        problems: list[str] = []

        if not self.knowledge_base_dir.exists():
            problems.append(
                f"knowledge_base_dir does not exist: "
                f"{self.knowledge_base_dir}"
            )
            return problems

        # Check that every expected clause tag has a playbook file.
        for tag in self.expected_clause_tags():
            expected = self.expected_file_for_tag(tag)

            if not expected.exists():
                problems.append(
                    f"missing playbook file for tag '{tag}': {expected}"
                )

        # Check for unexpected Markdown files.
        found_md = {
            p.stem
            for p in self.knowledge_base_dir.glob("*.md")
        }

        expected_stems = set(self.expected_clause_tags())

        unexpected = found_md - expected_stems

        if unexpected:
            problems.append(
                f"unexpected .md files with no matching clause tag: "
                f"{sorted(unexpected)} — either rename them to match "
                f"schemas.clause_schema.ClauseTag, or if this is a genuinely "
                f"new category, add it to the tag vocabulary in "
                f"docs/schema.md first."
            )

        return problems


# ---------------------------------------------------------------------------
# Single shared configuration instance
# ---------------------------------------------------------------------------
CONFIG = IndexConfig()
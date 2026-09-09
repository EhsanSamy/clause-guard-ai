from __future__ import annotations

import argparse
import shutil
import sys

from llama_index.core import (
    Settings,
    SimpleDirectoryReader,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)

from llama_index.core.embeddings import BaseEmbedding, MockEmbedding
from llama_index.core.node_parser import SentenceSplitter

from retrieval.index_config import CONFIG, IndexConfig


# ============================================================================
# Embedding model construction
# ============================================================================

def _build_embed_model(
    config: IndexConfig,
    force_mock: bool = False,
) -> BaseEmbedding:
    # ------------------------------------------------------------------------
    # MODE 1 — MOCK
    # ------------------------------------------------------------------------
    # --mock from the command line has the highest priority.
    if force_mock or config.embedding_provider == "mock":
        print(
            "[build_index] WARNING: using MockEmbedding — retrieval results "
            "are NOT semantically meaningful in this mode.",
            file=sys.stderr,
        )

        return MockEmbedding(
            embed_dim=config.mock_embedding_dim
        )

    # ------------------------------------------------------------------------
    # MODE 2 — LOCAL HUGGING FACE / SENTENCE TRANSFORMERS
    # ------------------------------------------------------------------------
    if config.embedding_provider == "local":
        try:
            from llama_index.embeddings.huggingface import (
                HuggingFaceEmbedding,
            )
        except ImportError as exc:
            raise RuntimeError(
                "embedding_provider='local' but the Hugging Face embedding "
                "integration is not installed.\n\n"
                "Install it with:\n"
                "    pip install llama-index-embeddings-huggingface"
            ) from exc

        print(
            "[build_index] using LOCAL Hugging Face embedding model:"
        )
        print(
            f"[build_index] model={config.embedding_model}"
        )
        print(
            "[build_index] no embedding API key is required."
        )

        try:
            return HuggingFaceEmbedding(
                model_name=config.embedding_model
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to initialize the local Hugging Face embedding "
                f"model {config.embedding_model!r}. "
                "Make sure the model name is valid and the required "
                "dependencies are installed."
            ) from exc

    # ------------------------------------------------------------------------
    # MODE 3 — OPENAI
    # ------------------------------------------------------------------------
    if config.embedding_provider == "openai":
        try:
            from llama_index.embeddings.openai import OpenAIEmbedding
        except ImportError as exc:
            raise RuntimeError(
                "embedding_provider='openai' but "
                "llama-index-embeddings-openai is not installed.\n\n"
                "Install it with:\n"
                "    pip install llama-index-embeddings-openai"
            ) from exc

        if not config.embedding_api_key:
            raise RuntimeError(
                "embedding_provider='openai' but no EMBEDDING_API_KEY "
                "or OPENAI_API_KEY is set.\n\n"
                "Set one in .env, or use:\n"
                "    EMBEDDING_PROVIDER=local\n"
                "for local Hugging Face embeddings."
            )

        print(
            "[build_index] using REAL OpenAI embedding model:"
        )
        print(
            f"[build_index] model={config.embedding_model}"
        )

        return OpenAIEmbedding(
            model=config.embedding_model,
            api_key=config.embedding_api_key,
        )

    # ------------------------------------------------------------------------
    # UNKNOWN PROVIDER
    # ------------------------------------------------------------------------
    raise ValueError(
        f"Unknown embedding_provider: {config.embedding_provider!r}. "
        "Expected one of: 'openai', 'local', 'mock'."
    )

# ============================================================================
# Playbook document loading
# ============================================================================
def _load_playbook_documents(config: IndexConfig):

    reader = SimpleDirectoryReader(
        input_dir=str(config.knowledge_base_dir),
        required_exts=[".md"],
        filename_as_id=True,
    )

    documents = reader.load_data()

    for doc in documents:
        file_name = doc.metadata.get("file_name", "")

        clause_tag = (
            file_name.rsplit(".", 1)[0]
            if file_name
            else "unknown"
        )

        doc.metadata["clause_tag"] = clause_tag

    return documents

# ============================================================================
# Build / load index
# ============================================================================
def build_index(
    config: IndexConfig = CONFIG,
    force: bool = False,
    force_mock: bool = False,
) -> VectorStoreIndex:
    # ------------------------------------------------------------------------
    # Validate knowledge base first
    # ------------------------------------------------------------------------

    problems = config.validate_knowledge_base()

    if problems:
        raise RuntimeError(
            "retrieval/knowledge_base/ failed validation before indexing:\n"
            + "\n".join(
                f"  - {problem}"
                for problem in problems
            )
        )

    # ------------------------------------------------------------------------
    # Resolve embedding provider
    # ------------------------------------------------------------------------
    embed_model = _build_embed_model(
        config,
        force_mock=force_mock,
    )

    # Tell LlamaIndex which embedding model to use.
    Settings.embed_model = embed_model

    # Configure how Markdown documents are split into nodes.
    Settings.node_parser = SentenceSplitter(
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    )


    if config.persist_dir.exists() and not force:
        print(
            f"[build_index] loading existing index from "
            f"{config.persist_dir}"
        )

        storage_context = StorageContext.from_defaults(
            persist_dir=str(config.persist_dir)
        )

        return load_index_from_storage(storage_context)

    if config.persist_dir.exists() and force:
        print(
            f"[build_index] --force set, wiping "
            f"{config.persist_dir}"
        )

        shutil.rmtree(config.persist_dir)
    
    print(
        f"[build_index] loading playbook docs from "
        f"{config.knowledge_base_dir}"
    )

    documents = _load_playbook_documents(config)

    print(
        f"[build_index] loaded {len(documents)} documents "
        f"(expected {len(config.expected_clause_tags())})"
    )

    print(
        "[build_index] building VectorStoreIndex..."
    )

    index = VectorStoreIndex.from_documents(
        documents,
        show_progress=True,
    )

    config.persist_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    index.storage_context.persist(
        persist_dir=str(config.persist_dir)
    )

    print(
        f"[build_index] persisted index to "
        f"{config.persist_dir}"
    )

    return index

# ============================================================================
# Standalone retrieval smoke test
# ============================================================================
def test_query(
    index: VectorStoreIndex,
    query_text: str,
    top_k: int = 1,
) -> None:

    retriever = index.as_retriever(
        similarity_top_k=top_k
    )

    results = retriever.retrieve(
        query_text
    )

    print(
        f"\n[test_query] query: {query_text!r}"
    )

    if not results:
        print(
            "[test_query] no results returned"
        )
        return

    for i, node in enumerate(
        results,
        start=1,
    ):
        tag = node.node.metadata.get(
            "clause_tag",
            "unknown",
        )

        file_name = node.node.metadata.get(
            "file_name",
            "unknown",
        )

        print(
            f"[test_query] #{i} "
            f"clause_tag={tag} "
            f"file={file_name} "
            f"score={node.score:.4f}"
        )

        preview = (
            node.node
            .get_content()
            .strip()
            .replace("\n", " ")
            [:120]
        )

        print(
            f"[test_query]      preview: "
            f"{preview}..."
        )

# ============================================================================
# Default smoke-test queries
# ============================================================================
DEFAULT_TEST_QUERIES = {
    "indemnification": (
        "Vendor agrees to indemnify, defend, and hold harmless Client from "
        "any and all claims, damages, liabilities, and expenses arising "
        "from or related to this Agreement."
    ),

    "limitation_of_liability": (
        "In no event shall either party's aggregate liability exceed the "
        "total fees paid in the twelve months preceding the claim."
    ),

    "auto_renewal": (
        "This Agreement shall automatically renew for successive one-year "
        "terms unless either party provides written notice of non-renewal "
        "at least ninety days prior to the end of the then-current term."
    ),

    "governing_law": (
        "This Agreement shall be governed by the laws of a jurisdiction "
        "mutually agreed upon at the time of dispute, with no forum "
        "selection clause specified."
    ),
}


# ============================================================================
# Command-line interface
# ============================================================================
def main() -> None:
    """
    Command-line entry point.
    """

    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "rebuild the index even if a persisted index already exists"
        ),
    )

    parser.add_argument(
        "--mock",
        action="store_true",
        help=(
            "force MockEmbedding regardless of EMBEDDING_PROVIDER"
        ),
    )

    parser.add_argument(
        "--test-query",
        type=str,
        default=None,
        help=(
            "run one query against the built index instead of "
            "the default smoke tests"
        ),
    )

    args = parser.parse_args()

    index = build_index(
        force=args.force,
        force_mock=args.mock,
    )

    # ------------------------------------------------------------------------
    # Custom query
    # ------------------------------------------------------------------------
    if args.test_query:
        test_query(
            index,
            args.test_query,
        )

    # ------------------------------------------------------------------------
    # Default smoke tests
    # ------------------------------------------------------------------------
    else:
        print(
            "\n[build_index] running default smoke test "
            "(one query per tag)..."
        )

        for expected_tag, query in DEFAULT_TEST_QUERIES.items():
            test_query(
                index,
                query,
            )

            print(
                f"[build_index]      "
                f"(expected clause_tag: {expected_tag})"
            )


# ============================================================================
# Module entry point
# ============================================================================
if __name__ == "__main__":
    main()
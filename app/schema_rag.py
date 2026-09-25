"""Phase 5: schema retrieval (RAG). Instead of dumping the full schema into
every prompt, embed each table's description once and retrieve only the
top-k most relevant tables per question.

Uses Chroma's default local embedding model (all-MiniLM-L6-v2, runs via
ONNX on-device) — no embedding API key needed, keeping this self-contained.
The index is persisted to ./chroma_db so it's built once, not on every call.
"""

import chromadb

from app.schema import TABLE_DESCRIPTIONS

CHROMA_PATH = "chroma_db"
COLLECTION_NAME = "aml_schema"
DEFAULT_K = 5


def get_collection():
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    return client.get_or_create_collection(COLLECTION_NAME)


def build_index(force: bool = False) -> None:
    collection = get_collection()
    if collection.count() > 0:
        if not force:
            return
        collection.delete(ids=list(TABLE_DESCRIPTIONS.keys()))

    collection.add(
        ids=list(TABLE_DESCRIPTIONS.keys()),
        documents=list(TABLE_DESCRIPTIONS.values()),
    )


def retrieve_relevant_tables(question: str, k: int = DEFAULT_K) -> list[str]:
    """Returns the names of the k tables whose description is most similar
    to the question, ranked most-relevant first."""
    build_index()
    collection = get_collection()
    results = collection.query(query_texts=[question], n_results=min(k, len(TABLE_DESCRIPTIONS)))
    return results["ids"][0]


def retrieve_schema_text(question: str, k: int = DEFAULT_K) -> str:
    table_names = retrieve_relevant_tables(question, k=k)
    return "\n\n".join(TABLE_DESCRIPTIONS[name] for name in table_names)


if __name__ == "__main__":
    import sys

    question = " ".join(sys.argv[1:]) or "show me last month's transactions and tell me which are suspicious"
    build_index(force=True)
    tables = retrieve_relevant_tables(question)
    print(f"Question: {question}")
    print(f"Retrieved tables (top {len(tables)}): {tables}")

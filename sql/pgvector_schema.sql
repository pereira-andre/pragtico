CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    manifest JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY,
    document_name TEXT NOT NULL,
    chunk_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding VECTOR,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS rag_chunks_document_chunk_idx
    ON rag_chunks (document_name, chunk_id);

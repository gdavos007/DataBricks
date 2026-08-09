# Weather Embeddings Pipeline

This pipeline processes weather documents from `weather_documents`, chunks the narrative text, generates embeddings, and stores them in `weather_embeddings` for RAG-based retrieval.

## Architecture

```
weather_documents → Chunk → Embed → weather_embeddings (pgvector)
                      ↓        ↓
                    800ch    384-dim
                    100 overlap  all-MiniLM-L6-v2
```

## Key Design Decisions

### 1. No Spark - Direct psycopg2 Writes

**Why?** `spark.write.jdbc` does not work reliably with Lakebase in this environment. Instead, we use:
- Direct `psycopg2` connections via the existing `lakebase.py` helper
- `execute_values()` from `psycopg2.extras` for efficient batch inserts
- Explicit `::vector` cast in SQL for pgvector compatibility

### 2. Chunking Strategy

**Parameters:**
- `CHUNK_SIZE = 800` characters
- `CHUNK_OVERLAP = 100` characters

**Rationale:**
- Most NWS text (alerts, forecast periods) is already short (< 500 chars)
- Chunking mainly benefits combined alert descriptions + instructions
- 800-char chunks capture full context while staying under model limits
- 100-char overlap ensures context continuity across chunk boundaries

**Alternative:** You could skip chunking entirely for short documents and only chunk long forecast discussions (AFD). The current approach is conservative and handles all cases.

### 3. Embedding Model

**Model:** `sentence-transformers/all-MiniLM-L6-v2`
- **Dimension:** 384
- **Why this model?**
  - Same as existing ticker news pipeline (maintains compatibility)
  - Fast inference (~10ms per sentence on CPU)
  - Good balance of quality vs speed
  - Well-suited for short text retrieval

**Alternative models:**
- `all-mpnet-base-v2` (768-dim) - Better quality, slower
- `all-distilroberta-v1` (768-dim) - Good for semantic search
- Must update `EMBEDDING_DIM` and table schema if you change models

### 4. pgvector Setup

**Extension:** Requires `pgvector` extension (already enabled in Lakebase)

**Column type:** `vector(384)` - fixed-dimension vector

**Index:** HNSW (Hierarchical Navigable Small World)
```sql
CREATE INDEX ... USING hnsw (embedding vector_cosine_ops)
```

**Why HNSW?**
- Fast approximate nearest neighbor search
- Better recall than IVFFlat for smaller datasets (< 1M rows)
- Supports cosine similarity (`<=>` operator)

**Alternative:** IVFFlat for very large datasets (> 1M embeddings)
```sql
CREATE INDEX ... USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
```

## Schema

### `weather_embeddings` Table

```sql
CREATE TABLE weather_embeddings (
    id TEXT PRIMARY KEY,              -- MD5 hash of document_id + chunk_index
    document_id TEXT NOT NULL,        -- FK to weather_documents.id
    chunk_index INTEGER NOT NULL,     -- 0-based chunk position
    chunk_text TEXT NOT NULL,         -- The actual text chunk
    embedding vector(384),            -- 384-dim embedding vector
    model_name TEXT NOT NULL,         -- "sentence-transformers/all-MiniLM-L6-v2"
    created_at TIMESTAMPTZ NOT NULL,  -- When embedding was generated
    UNIQUE(document_id, chunk_index)
);
```

**Indexes:**
1. `HNSW` on `embedding` for vector similarity search
2. B-tree on `document_id` for document lookups

## Usage

### Option 1: Run the Notebook (Recommended)

Open [Ingest Weather Embeddings](#notebook-3254005422154617) and run cells in order:

1. **Install Dependencies** - `pip install sentence-transformers`
2. **Configuration** - Set chunk size, model, batch size
3. **Create Table** - Initialize `weather_embeddings` with pgvector
4. **Load Model** - Download and cache the embedding model
5. **Fetch Documents** - Get unembedded weather documents
6. **Generate Embeddings** - Chunk and embed documents
7. **Write to Lakebase** - Batch insert via psycopg2
8. **Verify** - Query embeddings and test similarity search
9. **Run Pipeline** - Process all documents in batches

### Option 2: Run the Python Script

```bash
# Process all documents
python ingest_weather_embeddings.py

# Process in batches
python ingest_weather_embeddings.py --batch-size 50 --max-batches 5
```

**Arguments:**
- `--batch-size`: Documents per batch (default: 100)
- `--max-batches`: Maximum batches to process (default: all)

## Pipeline Flow

### 1. Fetch Unembedded Documents

Query finds documents that:
- Have no embeddings yet, OR
- Were updated after their embeddings were created

```sql
SELECT wd.* 
FROM weather_documents wd
LEFT JOIN weather_embeddings we ON wd.id = we.document_id
WHERE we.document_id IS NULL 
   OR wd.synced_at > we.created_at
```

### 2. Chunk Text

Sliding window with overlap:
```python
chunks = []
start = 0
while start < len(text):
    end = start + CHUNK_SIZE
    chunks.append(text[start:end])
    start = end - CHUNK_OVERLAP  # Move back for overlap
```

### 3. Generate Embeddings

Batch encode all chunks at once:
```python
chunk_embeddings = model.encode(chunks, show_progress_bar=False)
# Returns numpy array of shape (num_chunks, 384)
```

### 4. Write to Database

Use `execute_values()` for efficient batch insert:
```python
execute_values(
    cursor,
    insert_sql,
    embedding_rows,
    template="(%s, %s, %s, %s, %s::vector, %s, %s)",  # Cast to vector
    page_size=100
)
```

**Key:** The `%s::vector` cast tells PostgreSQL to treat the list as a vector type.

## Vector Similarity Search

### Find Similar Chunks

```sql
SELECT 
    document_id,
    chunk_text,
    1 - (embedding <=> $1::vector) as similarity
FROM weather_embeddings
ORDER BY embedding <=> $1::vector  -- Cosine distance
LIMIT 5
```

**Operators:**
- `<=>` - Cosine distance (0 = identical, 2 = opposite)
- `<->` - L2 distance (Euclidean)
- `<#>` - Inner product

**Similarity score:** `1 - distance` converts distance to similarity (0-1 scale)

## Performance

**Typical throughput:**
- Embedding: ~100 chunks/second (CPU)
- Writing: ~500 rows/second (psycopg2 batch insert)

**For 1000 weather documents:**
- Average 2 chunks per document = 2000 embeddings
- Embedding time: ~20 seconds
- Write time: ~4 seconds
- **Total: < 30 seconds**

## Troubleshooting

### "pgvector extension does not exist"

```sql
CREATE EXTENSION vector;
```

Requires superuser permissions. Contact your Lakebase admin if this fails.

### "cannot cast type text to vector"

Make sure you're using the `::vector` cast:
```python
template="(%s, %s, %s, %s, %s::vector, %s, %s)"  # Correct
template="(%s, %s, %s, %s, %s, %s, %s)"          # Wrong
```

### "HNSW index build failed"

Fall back to IVFFlat:
```sql
DROP INDEX idx_weather_embeddings_embedding_hnsw;
CREATE INDEX idx_weather_embeddings_embedding_ivfflat
ON weather_embeddings 
USING ivfflat (embedding vector_cosine_ops) 
WITH (lists = 100);
```

### Slow similarity search

Make sure the HNSW index was created:
```sql
\d weather_embeddings  -- Check indexes
```

If missing:
```sql
CREATE INDEX idx_weather_embeddings_embedding_hnsw
ON weather_embeddings 
USING hnsw (embedding vector_cosine_ops);
```

## Next Steps

1. **Build RAG Query Endpoint** - Add to Flask app:
   ```python
   @app.route("/weather/search", methods=["POST"])
   def search_weather():
       # Embed user query
       # Search weather_embeddings
       # Return top-k similar chunks
   ```

2. **Add to App** - Integrate with your existing `/ask` endpoint

3. **Hybrid Search** - Combine vector similarity with metadata filters:
   ```sql
   WHERE location = 'Chicago, IL' 
     AND source_type = 'alert'
   ORDER BY embedding <=> $1::vector
   ```

## Files

- `ingest_weather_embeddings.py` - Standalone Python script
- [Ingest Weather Embeddings](#notebook-3254005422154617) - Interactive notebook
- `lakebase.py` - Database connection helper
- `weather_client.py` - NWS API client (generates source documents)

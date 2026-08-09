# Weather RAG App - Quick Start Guide

## 🚀 Get Started in 5 Minutes

### Step 1: Setup Database Credentials

```bash
python setup_secrets.py
```

Paste your Lakebase connection URL when prompted:
```
postgresql://user:password@host:port/database?sslmode=require
```

### Step 2: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 3: Start the Flask App

```bash
python app.py
```

App will run on http://localhost:8080

### Step 4: Sync Some Weather Data

In another terminal:

```bash
python test_weather_sync.py
```

Or manually:

```bash
curl -X POST http://localhost:8080/weather/sync \
  -H "Content-Type: application/json" \
  -d '{
    "locations": ["Chicago, IL", "San Diego, CA"],
    "limit": 50,
    "include_alerts": true,
    "include_forecasts": true,
    "include_discussions": true
  }'
```

### Step 5: Generate Embeddings

```bash
python ingest_weather_embeddings.py
```

Or use the notebook for interactive exploration:
* Open [Ingest Weather Embeddings](#notebook-3254005422154617)
* Run all cells

### Step 6: Search!

```bash
python test_weather_search.py
```

Or manually:

```bash
curl -X POST http://localhost:8080/weather/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "severe thunderstorm warnings",
    "top_k": 5
  }'
```

## 📊 Example Response

```json
{
  "query": "severe thunderstorm warnings",
  "top_k": 5,
  "total_embeddings": 1247,
  "results": [
    {
      "document_id": "urn:alert:us-gov:nws:...",
      "location": "Chicago, IL",
      "headline": "Severe Thunderstorm Warning",
      "narrative_text": "Full document...",
      "chunk_text": "Severe thunderstorms with damaging winds...",
      "similarity": 0.8734
    }
  ]
}
```

## 🛠️ Troubleshooting

### "Embedding model not available"

**Problem:** Flask app couldn't load the sentence-transformers model

**Solution:**
```bash
pip install sentence-transformers torch
```

Then restart the app.

### "No embeddings available"

**Problem:** You haven't run the embedding pipeline yet

**Solution:**
```bash
python ingest_weather_embeddings.py
```

### "pgvector extension does not exist"

**Problem:** pgvector extension not enabled in Lakebase

**Solution:** Contact your Lakebase admin or run:
```sql
CREATE EXTENSION vector;
```

### "cannot connect to database"

**Problem:** Database credentials not set or incorrect

**Solution:**
```bash
python setup_secrets.py
```

Make sure to paste the correct connection URL.

### Slow search queries

**Problem:** HNSW index not created

**Solution:** Run the embedding pipeline notebook which creates the index, or manually:
```sql
CREATE INDEX idx_weather_embeddings_embedding_hnsw
ON weather_embeddings 
USING hnsw (embedding vector_cosine_ops);
```

## 📚 Architecture Overview

```
┌──────────────────────────────────┐
│   Part 1: Ingest (Data Sync)      │
│   NWS API → Flask → Lakebase       │
│   weather_documents table         │
└────────────────┬─────────────────┘
                 │
                 │
┌────────────────┴─────────────────┐
│  Part 2: Vectorize (Embeddings)    │
│  Chunk → Embed → Lakebase          │
│  weather_embeddings (pgvector)    │
└────────────────┬─────────────────┘
                 │
                 │
┌────────────────┴─────────────────┐
│   Part 3: Retrieve (Search)        │
│   Query → Embed → Similarity       │
│   /weather/search endpoint        │
└──────────────────────────────────┘
```

## 📝 API Reference

### Sync Weather Data

**Endpoint:** `POST /weather/sync`

**Body:**
```json
{
  "locations": ["Chicago, IL", "41.88,-87.63"],
  "limit": 50,
  "include_alerts": true,
  "include_forecasts": true,
  "include_discussions": true
}
```

**Response:**
```json
{
  "synced": 42,
  "locations": ["Chicago, IL"],
  "errors": null
}
```

### Semantic Search

**Endpoint:** `POST /weather/search`

**Body:**
```json
{
  "query": "severe weather warnings",
  "top_k": 5
}
```

**Response:**
```json
{
  "query": "severe weather warnings",
  "top_k": 5,
  "total_embeddings": 1247,
  "results": [
    {
      "document_id": "...",
      "location": "Chicago, IL",
      "headline": "Severe Thunderstorm Warning",
      "narrative_text": "...",
      "chunk_text": "...",
      "similarity": 0.87
    }
  ]
}
```

### List Documents

**Endpoint:** `GET /weather/documents`

**Query Params:**
* `limit` - Number of documents (default: 100)
* `source_type` - Filter by "alert" or "forecast"
* `location` - Filter by location (partial match)

**Example:**
```
GET /weather/documents?limit=10&source_type=alert&location=Chicago
```

## 🔧 Configuration

### Environment Variables

```bash
export LAKEBASE_SECRET_SCOPE="weather-db"  # Default
export LAKEBASE_SECRET_KEY="lakebase-url"  # Default
```

### Model Configuration

In `app.py` and `ingest_weather_embeddings.py`:

```python
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"  # 384-dim
```

To use a different model, update:
1. Model name in both files
2. `EMBEDDING_DIM` constant
3. Vector column in database: `vector(NEW_DIM)`
4. Re-run embedding pipeline

### Chunking Configuration

In `ingest_weather_embeddings.py`:

```python
CHUNK_SIZE = 800      # Characters per chunk
CHUNK_OVERLAP = 100   # Overlap between chunks
```

## 📊 Performance Tips

### 1. Batch Processing

Process embeddings in larger batches:
```bash
python ingest_weather_embeddings.py --batch-size 100
```

### 2. Index Strategy

For large datasets (> 1M embeddings), use IVFFlat instead of HNSW:
```sql
CREATE INDEX ... USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

### 3. Filter Before Similarity

Combine metadata filters with vector search:
```sql
WHERE d.location = 'Chicago, IL' 
  AND d.source_type = 'alert'
ORDER BY e.embedding <=> $1::vector
```

### 4. Cache the Embedding Model

The model is loaded once at Flask startup, not per-request. Keep the app running.

## 📚 Further Reading

* [README.md](#file-3254005422154470) - Full documentation
* [EMBEDDING_PIPELINE.md](#file-3254005422154618) - Detailed embedding pipeline guide
* [Ingest Weather Embeddings](#notebook-3254005422154617) - Interactive notebook

## ❓ FAQ

**Q: Can I use a different embedding model?**

A: Yes! Update the model name in both `app.py` and `ingest_weather_embeddings.py`, adjust the dimension, and re-run the embedding pipeline.

**Q: How often should I run the embedding pipeline?**

A: After each sync, or on a schedule (e.g., hourly). The pipeline only processes unembedded documents.

**Q: Can I search without embeddings?**

A: No, the `/weather/search` endpoint requires embeddings. Use `/weather/documents` for metadata-only queries.

**Q: Why pgvector instead of a dedicated vector database?**

A: pgvector keeps everything in one database (documents + embeddings), simplifies deployment, and performs well for millions of vectors.

**Q: What's the search latency?**

A: Milliseconds for < 1M embeddings with HNSW index. For larger datasets, consider IVFFlat.

**Q: Can I use this for other text sources?**

A: Absolutely! The architecture is generic. Replace `weather_client.py` with your own data source.

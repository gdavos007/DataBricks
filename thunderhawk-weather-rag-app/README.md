# Weather RAG App

A Flask-based application that fetches weather data from the National Weather Service (NWS) API and stores it in Lakebase (Databricks-managed Postgres) for RAG-based question answering.

## Architecture

```
NWS API → weather_client.py → Flask App → Lakebase Postgres
                                              ↓
                                      weather_documents
                                              ↓
                              ingest_weather_embeddings.py
                                              ↓
                                      weather_embeddings (pgvector)
                                              ↓
                                    /weather/search (RAG)
```

## Features

* **Weather Alerts**: Fetch active weather alerts (warnings, watches, advisories)
* **Forecast Data**: Get detailed forecast periods with narratives
* **Forecast Discussions**: Retrieve Area Forecast Discussions (AFD) - detailed meteorologist analysis
* **Location Support**: 
  - Lat/lon coordinates: `"41.88,-87.63"`
  - City/state format: `"Chicago, IL"`, `"Austin, TX"`
* **Normalized Documents**: All weather data normalized into a consistent schema for RAG
* **Vector Embeddings**: Automatic chunking and embedding of weather narratives using `sentence-transformers`
* **Semantic Search**: Fast similarity search using pgvector (cosine distance)

## Database Schema

**weather_documents** table:
```sql
CREATE TABLE weather_documents (
    id TEXT PRIMARY KEY,
    location TEXT NOT NULL,
    source_type TEXT NOT NULL,  -- 'alert' or 'forecast'
    headline TEXT,
    event TEXT,
    narrative_text TEXT,        -- Main text to embed
    issued_at TIMESTAMPTZ,
    effective_at TIMESTAMPTZ,
    payload JSONB NOT NULL,     -- Raw NWS response
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**weather_embeddings** table (requires pgvector extension):
```sql
CREATE TABLE weather_embeddings (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding vector(384),      -- sentence-transformers/all-MiniLM-L6-v2
    model_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_id, chunk_index)
);

-- HNSW index for fast cosine similarity search
CREATE INDEX idx_weather_embeddings_embedding_hnsw
ON weather_embeddings USING hnsw (embedding vector_cosine_ops);
```

## Setup

### 1. Store Database Credentials

**Option A: Run the setup script (recommended)**
```bash
python setup_secrets.py
```

This creates a Databricks secret scope `weather-db` and stores your Lakebase URL securely.

**Option B: Manual setup**
```python
from databricks.sdk import WorkspaceClient
import base64

w = WorkspaceClient()

# Create scope
w.secrets.create_scope(scope="weather-db")

# Store connection URL (base64-encoded)
url = "postgresql://student:npg_1igvhNWJru9y@ep-super-flower-d86lslmj.database.us-east-2.cloud.databricks.com/databricks_postgres?sslmode=require"
encoded = base64.b64encode(url.encode()).decode()
w.secrets.put_secret(scope="weather-db", key="lakebase-url", string_value=encoded)
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run the App

```bash
python app.py
```

The app will start on `http://0.0.0.0:8080`

## API Endpoints

### Health Check
```bash
GET /healthz
```

### List Weather Documents
```bash
GET /weather/documents?limit=100&source_type=alert&location=Chicago
```

Query parameters:
- `limit`: Number of documents to return (default: 100)
- `source_type`: Filter by "alert" or "forecast"
- `location`: Filter by location (partial match)

### Sync Weather Data
```bash
POST /weather/sync
Content-Type: application/json

{
  "locations": ["Chicago, IL", "Austin, TX", "41.88,-87.63"],
  "limit": 50,
  "include_alerts": true,
  "include_forecasts": true,
  "include_discussions": true
}
```

Response:
```json
{
  "synced": 42,
  "locations": ["Chicago, IL", "Austin, TX", "41.88,-87.63"],
  "errors": null
}
```

### Semantic Search (RAG)
```bash
POST /weather/search
Content-Type: application/json

{
  "query": "risk of flooding near rivers",
  "top_k": 5
}
```

Response:
```json
{
  "query": "risk of flooding near rivers",
  "top_k": 5,
  "total_embeddings": 1247,
  "results": [
    {
      "document_id": "...",
      "location": "Chicago, IL",
      "headline": "Flood Warning",
      "narrative_text": "Full document text...",
      "chunk_text": "Relevant chunk of text...",
      "similarity": 0.8734
    }
  ]
}
```

**Parameters:**
- `query` (required): Natural language search query
- `top_k` (optional): Number of results to return (default: 5, range: 1-20)

**Note**: Run the embedding pipeline first to populate `weather_embeddings`

## Embedding Pipeline

After syncing weather data, run the embedding pipeline to enable semantic search:

### Option 1: Run the Notebook (Recommended)
Open and run the [Ingest Weather Embeddings](#notebook-3254005422154617) notebook.

### Option 2: Run the Python Script
```bash
python ingest_weather_embeddings.py

# Or with options:
python ingest_weather_embeddings.py --batch-size 50 --max-batches 5
```

See [EMBEDDING_PIPELINE.md](#file-3254005422154618) for detailed documentation.

## Testing

### Test Weather Sync
```bash
python test_weather_sync.py
```

### Test Semantic Search
```bash
python test_weather_search.py
```

### Or use curl:
```bash
# Sync weather for Chicago and Austin
curl -X POST http://localhost:8080/weather/sync \
  -H "Content-Type: application/json" \
  -d '{"locations": ["Chicago, IL", "Austin, TX"], "limit": 50}'

# List synced documents
curl http://localhost:8080/weather/documents?limit=10

# Semantic search
curl -X POST http://localhost:8080/weather/search \
  -H "Content-Type: application/json" \
  -d '{"query": "severe weather warnings", "top_k": 5}'
```

## Files

**Core Application:**
* `app.py` - Flask application with sync and search endpoints
* `weather_client.py` - NWS API client with normalization functions
* `lakebase.py` - Postgres connection helper

**Embedding Pipeline:**
* `ingest_weather_embeddings.py` - Production embedding script
* [Ingest Weather Embeddings](#notebook-3254005422154617) - Interactive notebook
* [EMBEDDING_PIPELINE.md](#file-3254005422154618) - Pipeline documentation

**Setup & Testing:**
* `setup_secrets.py` - Store database credentials securely
* `test_weather_sync.py` - Test the sync endpoint
* `test_weather_search.py` - Test the search endpoint
* `requirements.txt` - Python dependencies
* `README.md` - This file

## NWS API Details

**Base URL**: `https://api.weather.gov`

**Key Endpoints Used**:
- `GET /points/{lat},{lon}` - Resolve location to grid point
- `GET /alerts/active` - Fetch active weather alerts
- `GET /gridpoints/{office}/{gridX},{gridY}/forecast` - Get forecast periods
- `GET /products/types/AFD/locations/{office}` - Get forecast discussions

**No authentication required** - NWS API is public and free

**Rate Limits**: Be respectful, the NWS asks that you include a User-Agent header (already configured)

## Complete Workflow

### 1. Setup (One-time)
```bash
# Store database credentials
python setup_secrets.py

# Install dependencies
pip install -r requirements.txt
```

### 2. Start the App
```bash
python app.py
```

### 3. Sync Weather Data
```bash
curl -X POST http://localhost:8080/weather/sync \
  -H "Content-Type: application/json" \
  -d '{"locations": ["Chicago, IL", "San Diego, CA"], "limit": 50}'
```

### 4. Generate Embeddings
```bash
python ingest_weather_embeddings.py
```

### 5. Search
```bash
curl -X POST http://localhost:8080/weather/search \
  -H "Content-Type: application/json" \
  -d '{"query": "severe thunderstorm warnings", "top_k": 5}'
```

## Example Use Cases

* "What weather alerts are active in Chicago right now?"
* "Give me the forecast for Austin this week"
* "Summarize the latest meteorologist discussion for Denver"
* "Are there any severe weather warnings in the Midwest?"
* "Find documents about flooding risks near rivers"
* "Show me winter storm advisories"
* "What are the high wind forecasts?"

## Technical Details

**Embedding Model:** `sentence-transformers/all-MiniLM-L6-v2`
* Dimension: 384
* Same model as the ticker news pipeline for compatibility
* Loaded once at Flask app startup (not per-request)

**Chunking Strategy:**
* Chunk size: 800 characters
* Overlap: 100 characters
* Sliding window approach

**Vector Search:**
* pgvector extension with HNSW index
* Cosine similarity (`<=>` operator)
* Similarity score: `1 - distance` (0-1 scale)

**Performance:**
* ~100 chunks/second embedding throughput
* ~500 rows/second write throughput
* Millisecond-scale similarity search

"""Weather RAG App - Flask API for NWS Weather Data.

Fetches weather alerts and forecasts from the National Weather Service API
and stores them in Lakebase for RAG-based question answering.
"""

import json
import logging

from flask import Flask, jsonify, request, render_template_string
from sentence_transformers import SentenceTransformer

import lakebase
from weather_client import (
    WeatherClient,
    normalize_alert_to_document,
    normalize_forecast_discussion_to_document,
    normalize_forecast_period_to_document,
    parse_location,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("weather-app")

# Load embedding model once at module level (not per-request)
# Same model as used in embedding ingestion pipeline
EMBEDDING_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
logger.info(f"Loading embedding model: {EMBEDDING_MODEL_NAME}...")
try:
    embedding_model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    logger.info("✓ Embedding model loaded successfully")
except Exception as e:
    logger.error(f"Failed to load embedding model: {e}")
    embedding_model = None

app = Flask(__name__)

WEATHER_DOCUMENTS_TABLE = "weather_documents"
WEATHER_EMBEDDINGS_TABLE = "weather_embeddings"

# Initialize database schema on app startup
try:
    lakebase.init_db()
    logger.info("✓ Database initialized successfully")
except Exception as e:
    logger.error(f"Failed to initialize database: {e}")

# Modern dashboard HTML template
DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Weather RAG Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        .theme-dark { background: #1a1a2e; color: #eee; }
        .theme-light { background: #f8f9fa; color: #333; }
    </style>
</head>
<body class="theme-dark min-h-screen transition-colors">
    <div class="container mx-auto p-6 max-w-6xl">
        <!-- Header -->
        <header class="flex items-center justify-between mb-8">
            <div>
                <h1 class="text-3xl font-bold">⚡ Weather RAG Dashboard</h1>
                <p class="text-gray-400 mt-1">NWS Weather Data + Semantic Search</p>
            </div>
            <button id="themeToggle" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg transition">
                🌙 Dark
            </button>
        </header>

        <!-- Weather Sync Section -->
        <section class="bg-gray-800 rounded-lg p-6 mb-6 shadow-lg">
            <h2 class="text-xl font-semibold mb-4">🌦️ Sync Weather Data</h2>
            <div class="flex gap-3 mb-4">
                <input 
                    type="text" 
                    id="locationInput" 
                    placeholder="e.g. Chicago, IL or 41.88,-87.63 or TX" 
                    class="flex-1 px-4 py-2 bg-gray-700 border border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <button 
                    id="syncBtn" 
                    class="px-6 py-2 bg-green-600 hover:bg-green-700 rounded-lg font-medium transition"
                >
                    Sync Location
                </button>
            </div>
            <div id="syncStatus" class="text-sm text-gray-400"></div>
        </section>

        <!-- Semantic Search Section -->
        <section class="bg-gray-800 rounded-lg p-6 mb-6 shadow-lg">
            <h2 class="text-xl font-semibold mb-4">🔍 Semantic Search</h2>
            <div class="flex gap-3 mb-4">
                <input 
                    type="text" 
                    id="searchInput" 
                    placeholder="Ask about weather: 'flooding risk' or 'severe thunderstorms'" 
                    class="flex-1 px-4 py-2 bg-gray-700 border border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <button 
                    id="searchBtn" 
                    class="px-6 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg font-medium transition"
                >
                    Search
                </button>
            </div>
            <div class="flex items-center gap-3 mb-4">
                <label class="text-sm text-gray-400">Top K:</label>
                <input 
                    type="range" 
                    id="topKSlider" 
                    min="1" 
                    max="10" 
                    value="5" 
                    class="flex-1"
                />
                <span id="topKValue" class="text-sm font-medium">5</span>
            </div>
            <div id="searchResults"></div>
        </section>
    </div>

    <script>
        const body = document.body;
        const themeToggle = document.getElementById('themeToggle');
        const topKSlider = document.getElementById('topKSlider');
        const topKValue = document.getElementById('topKValue');
        const syncBtn = document.getElementById('syncBtn');
        const searchBtn = document.getElementById('searchBtn');
        const locationInput = document.getElementById('locationInput');
        const searchInput = document.getElementById('searchInput');
        const syncStatus = document.getElementById('syncStatus');
        const searchResults = document.getElementById('searchResults');

        // Theme toggle
        themeToggle.addEventListener('click', () => {
            if (body.classList.contains('theme-dark')) {
                body.classList.replace('theme-dark', 'theme-light');
                themeToggle.textContent = '☀️ Light';
            } else {
                body.classList.replace('theme-light', 'theme-dark');
                themeToggle.textContent = '🌙 Dark';
            }
        });

        // Top K slider
        topKSlider.addEventListener('input', (e) => {
            topKValue.textContent = e.target.value;
        });

        // Sync weather data
        syncBtn.addEventListener('click', async () => {
            const location = locationInput.value.trim();
            if (!location) {
                syncStatus.innerHTML = '<span class="text-red-400">⚠️ Please enter a location</span>';
                return;
            }

            syncBtn.disabled = true;
            syncBtn.textContent = 'Syncing...';
            syncStatus.innerHTML = '<span class="text-blue-400">⏳ Fetching weather data...</span>';

            try {
                const response = await fetch('/weather/sync', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        locations: [location],
                        limit: 50,
                        include_alerts: true,
                        include_forecasts: true,
                        include_discussions: true
                    })
                });

                const data = await response.json();
                if (data.synced > 0) {
                    syncStatus.innerHTML = `<span class="text-green-400">✅ Synced ${data.synced} documents for ${data.locations.join(', ')}</span>`;
                } else {
                    syncStatus.innerHTML = '<span class="text-yellow-400">⚠️ No new data synced</span>';
                }

                if (data.errors && data.errors.length > 0) {
                    syncStatus.innerHTML += `<br><span class="text-red-400">Errors: ${JSON.stringify(data.errors)}</span>`;
                }
            } catch (error) {
                syncStatus.innerHTML = `<span class="text-red-400">❌ Error: ${error.message}</span>`;
            } finally {
                syncBtn.disabled = false;
                syncBtn.textContent = 'Sync Location';
            }
        });

        // Semantic search
        searchBtn.addEventListener('click', async () => {
            const query = searchInput.value.trim();
            const topK = parseInt(topKSlider.value);

            if (!query) {
                searchResults.innerHTML = '<p class="text-red-400">⚠️ Please enter a search query</p>';
                return;
            }

            searchBtn.disabled = true;
            searchBtn.textContent = 'Searching...';
            searchResults.innerHTML = '<p class="text-blue-400">⏳ Searching...</p>';

            try {
                const response = await fetch('/weather/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query, top_k: topK })
                });

                const data = await response.json();

                if (data.results && data.results.length > 0) {
                    const resultsHTML = data.results.map((r, idx) => `
                        <div class="bg-gray-700 rounded-lg p-4 mb-3 border border-gray-600">
                            <div class="flex justify-between items-start mb-2">
                                <h3 class="font-semibold text-lg">${r.headline || 'Weather Update'}</h3>
                                <span class="text-sm bg-blue-600 px-2 py-1 rounded">${(r.similarity * 100).toFixed(1)}%</span>
                            </div>
                            <p class="text-sm text-gray-400 mb-2">📍 ${r.location}</p>
                            <p class="text-sm">${r.chunk_text}</p>
                        </div>
                    `).join('');
                    searchResults.innerHTML = resultsHTML;
                } else {
                    searchResults.innerHTML = '<p class="text-yellow-400">No results found. Try syncing weather data first.</p>';
                }
            } catch (error) {
                searchResults.innerHTML = `<p class="text-red-400">❌ Error: ${error.message}</p>`;
            } finally {
                searchBtn.disabled = false;
                searchBtn.textContent = 'Search';
            }
        });

        // Allow Enter key for inputs
        locationInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') syncBtn.click();
        });
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') searchBtn.click();
        });
    </script>
</body>
</html>
''';


def ensure_weather_table():
    """Create the weather_documents table in Lakebase if it doesn't exist yet."""
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {WEATHER_DOCUMENTS_TABLE} (
            id TEXT PRIMARY KEY,
            location TEXT NOT NULL,
            source_type TEXT NOT NULL,
            headline TEXT,
            event TEXT,
            narrative_text TEXT,
            issued_at TIMESTAMPTZ,
            effective_at TIMESTAMPTZ,
            payload JSONB NOT NULL,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # Create indexes for common queries
    lakebase.run_write(
        f"CREATE INDEX IF NOT EXISTS idx_{WEATHER_DOCUMENTS_TABLE}_location "
        f"ON {WEATHER_DOCUMENTS_TABLE} (location)"
    )
    lakebase.run_write(
        f"CREATE INDEX IF NOT EXISTS idx_{WEATHER_DOCUMENTS_TABLE}_source_type "
        f"ON {WEATHER_DOCUMENTS_TABLE} (source_type)"
    )
    lakebase.run_write(
        f"CREATE INDEX IF NOT EXISTS idx_{WEATHER_DOCUMENTS_TABLE}_issued_at "
        f"ON {WEATHER_DOCUMENTS_TABLE} (issued_at DESC)"
    )


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.errorhandler(Exception)
def handle_exception(err):
    """Ensure all unhandled errors return JSON."""
    logger.exception("Unhandled exception while processing request")
    status_code = getattr(err, "code", 500)
    if not isinstance(status_code, int):
        status_code = 500
    return jsonify({"error": str(err)}), status_code


@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


@app.route("/weather/documents")
def list_weather_documents():
    """List weather documents already synced into Lakebase."""
    limit = int(request.args.get("limit", 100))
    source_type = request.args.get("source_type")  # Filter by alert/forecast
    location = request.args.get("location")  # Filter by location

    query = f"SELECT * FROM {WEATHER_DOCUMENTS_TABLE} WHERE 1=1"
    params = []

    if source_type:
        query += " AND source_type = %s"
        params.append(source_type)
    if location:
        query += " AND location ILIKE %s"
        params.append(f"%{location}%")

    query += " ORDER BY synced_at DESC LIMIT %s"
    params.append(limit)

    rows = lakebase.run_query(query, tuple(params))
    return jsonify(rows)


@app.route("/weather/search", methods=["POST"])
def search_weather():
    """
    Semantic search over weather embeddings using vector similarity.

    Body: {
        "query": "risk of flooding near rivers",
        "top_k": 5
    }

    Returns: [
        {
            "document_id": "...",
            "location": "Chicago, IL",
            "headline": "Flood Warning",
            "narrative_text": "...",
            "chunk_text": "...",
            "similarity": 0.87
        },
        ...
    ]
    """
    # Validate embedding model is loaded
    if embedding_model is None:
        return jsonify({"error": "Embedding model not available"}), 503

    # Parse request body
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400

    body = request.json
    query_text = body.get("query")
    top_k = body.get("top_k", 5)

    # Validate query
    if not query_text or not isinstance(query_text, str) or not query_text.strip():
        return jsonify({"error": "Missing or invalid 'query' field"}), 400

    # Validate and clamp top_k
    try:
        top_k = int(top_k)
        if top_k < 1:
            top_k = 1
        elif top_k > 20:
            top_k = 20
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid 'top_k' value (must be integer 1-20)"}), 400

    logger.info(f"Search query: '{query_text}' (top_k={top_k})")

    try:
        # Check if embeddings table exists and has data
        count_result = lakebase.run_query(
            f"SELECT COUNT(*) as count FROM {WEATHER_EMBEDDINGS_TABLE}"
        )
        embedding_count = count_result[0]["count"] if count_result else 0

        if embedding_count == 0:
            return jsonify({
                "results": [],
                "message": "No embeddings available. Run the embedding pipeline first."
            })

        # Embed the query using the same model as ingestion
        query_embedding = embedding_model.encode([query_text])[0].tolist()

        # Search for similar chunks using pgvector cosine similarity
        # <=> is the cosine distance operator (0 = identical, 2 = opposite)
        # Similarity = 1 - distance for a 0-1 scale
        search_sql = f"""
            SELECT 
                d.id as document_id,
                d.location,
                d.headline,
                d.narrative_text,
                e.chunk_text,
                1 - (e.embedding <=> %s::vector) AS similarity
            FROM {WEATHER_EMBEDDINGS_TABLE} e
            JOIN {WEATHER_DOCUMENTS_TABLE} d ON d.id = e.document_id
            ORDER BY e.embedding <=> %s::vector
            LIMIT %s
        """

        results = lakebase.run_query(search_sql, (query_embedding, query_embedding, top_k))

        logger.info(f"Found {len(results)} results")

        return jsonify({
            "query": query_text,
            "top_k": top_k,
            "results": results,
            "total_embeddings": embedding_count,
        })

    except Exception as e:
        logger.exception(f"Search failed: {e}")
        return jsonify({"error": f"Search failed: {str(e)}"}), 500


@app.route("/weather/sync", methods=["POST"])
def sync_weather_from_nws():
    """
    Fetch weather alerts and forecasts from the NWS API for given locations
    and upsert them into the weather_documents table.

    Body: {
        "locations": ["Chicago, IL", "Austin, TX", "41.88,-87.63"],
        "limit": 50,
        "include_alerts": true,
        "include_forecasts": true,
        "include_discussions": true
    }

    Returns: {"synced": count, "locations": [...], "errors": [...]}
    """
    ensure_weather_table()
    client = WeatherClient()

    body = request.json if request.is_json else {}
    locations = body.get("locations", [])
    limit = int(body.get("limit", 50))
    include_alerts = body.get("include_alerts", True)
    include_forecasts = body.get("include_forecasts", True)
    include_discussions = body.get("include_discussions", True)

    if not locations:
        return jsonify({"error": "No locations provided"}), 400

    total_synced = 0
    processed_locations = []
    errors = []

    for location_str in locations:
        try:
            # Parse location to lat/lon
            loc_info = parse_location(location_str)
            lat = loc_info["lat"]
            lon = loc_info["lon"]
            display_location = loc_info["display"]

            logger.info(f"Processing location: {display_location} ({lat}, {lon})")

            # Resolve to NWS grid point
            gridpoint = client.resolve_location_to_gridpoint(lat, lon)
            office = gridpoint.get("office")

            documents = []

            # 1. Fetch active alerts
            if include_alerts:
                try:
                    alerts = client.get_active_alerts(lat=lat, lon=lon, limit=limit)
                    for alert in alerts:
                        doc = normalize_alert_to_document(alert, display_location)
                        documents.append(doc)
                    logger.info(f"Fetched {len(alerts)} alerts for {display_location}")
                except Exception as e:
                    logger.warning(f"Failed to fetch alerts for {display_location}: {e}")
                    errors.append({"location": display_location, "type": "alerts", "error": str(e)})

            # 2. Fetch forecast periods
            if include_forecasts:
                try:
                    forecast_periods = client.get_forecast(gridpoint)
                    for period in forecast_periods[:limit]:  # Limit forecast periods
                        doc = normalize_forecast_period_to_document(period, display_location, office)
                        documents.append(doc)
                    logger.info(f"Fetched {len(forecast_periods)} forecast periods for {display_location}")
                except Exception as e:
                    logger.warning(f"Failed to fetch forecasts for {display_location}: {e}")
                    errors.append({"location": display_location, "type": "forecasts", "error": str(e)})

            # 3. Fetch forecast discussion (AFD)
            if include_discussions and office:
                try:
                    discussion = client.get_forecast_discussion(office)
                    if discussion:
                        doc = normalize_forecast_discussion_to_document(
                            discussion, display_location, office
                        )
                        documents.append(doc)
                        logger.info(f"Fetched forecast discussion for {display_location}")
                except Exception as e:
                    logger.warning(f"Failed to fetch discussion for {display_location}: {e}")
                    errors.append({"location": display_location, "type": "discussion", "error": str(e)})

            # Upsert all documents for this location
            if documents:
                synced = _upsert_weather_documents(documents)
                total_synced += synced
                logger.info(f"Synced {synced} documents for {display_location}")
                
                # Generate embeddings for newly synced documents
                document_ids = [doc["id"] for doc in documents]
                try:
                    embedded = _generate_embeddings_for_documents(document_ids)
                    logger.info(f"Generated {embedded} embeddings for {display_location}")
                except Exception as e:
                    logger.warning(f"Failed to generate embeddings for {display_location}: {e}")
                    errors.append({"location": display_location, "type": "embeddings", "error": str(e)})

            processed_locations.append(display_location)

        except Exception as e:
            logger.exception(f"Failed to process location {location_str}")
            errors.append({"location": location_str, "type": "general", "error": str(e)})

    return jsonify(
        {
            "synced": total_synced,
            "locations": processed_locations,
            "errors": errors if errors else None,
        }
    )


def _upsert_weather_documents(documents: list[dict]) -> int:
    """Upsert a batch of weather documents into Lakebase."""
    count = 0
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            for doc in documents:
                cur.execute(
                    f"""
                    INSERT INTO {WEATHER_DOCUMENTS_TABLE} (
                        id, location, source_type, headline, event,
                        narrative_text, issued_at, effective_at, payload, synced_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE
                        SET location = EXCLUDED.location,
                            source_type = EXCLUDED.source_type,
                            headline = EXCLUDED.headline,
                            event = EXCLUDED.event,
                            narrative_text = EXCLUDED.narrative_text,
                            issued_at = EXCLUDED.issued_at,
                            effective_at = EXCLUDED.effective_at,
                            payload = EXCLUDED.payload,
                            synced_at = EXCLUDED.synced_at
                    """,
                    (
                        doc["id"],
                        doc["location"],
                        doc["source_type"],
                        doc.get("headline"),
                        doc.get("event"),
                        doc.get("narrative_text"),
                        doc.get("issued_at"),
                        doc.get("effective_at"),
                        json.dumps(doc.get("payload", {})),
                        doc["synced_at"],
                    ),
                )
                count += 1
            conn.commit()
    return count


def _generate_embeddings_for_documents(document_ids: list[str]) -> int:
    """Generate embeddings for newly synced documents.
    
    Args:
        document_ids: List of document IDs to process
    
    Returns:
        Number of embedding records created
    """
    if not document_ids or embedding_model is None:
        return 0
    
    CHUNK_SIZE = 800
    CHUNK_OVERLAP = 100
    
    def chunk_text(text: str) -> list[str]:
        """Split text into overlapping chunks."""
        if not text or len(text) <= CHUNK_SIZE:
            return [text] if text else []
        
        chunks = []
        start = 0
        while start < len(text):
            end = start + CHUNK_SIZE
            chunk = text[start:end]
            chunks.append(chunk)
            start += (CHUNK_SIZE - CHUNK_OVERLAP)
        return chunks
    
    embedded_count = 0
    
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            # Fetch documents that need embedding
            cur.execute(
                f"""
                SELECT id, narrative_text 
                FROM {WEATHER_DOCUMENTS_TABLE}
                WHERE id = ANY(%s) AND narrative_text IS NOT NULL
                """,
                (document_ids,)
            )
            documents = cur.fetchall()
            
            for doc in documents:
                doc_id = doc["id"]
                narrative = doc["narrative_text"]
                
                if not narrative:
                    continue
                
                # Chunk the narrative text
                chunks = chunk_text(narrative)
                
                # Generate embeddings for all chunks
                if chunks:
                    embeddings = embedding_model.encode(chunks)
                    
                    # Insert embeddings
                    for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                        cur.execute(
                            f"""
                            INSERT INTO {WEATHER_EMBEDDINGS_TABLE} 
                            (document_id, chunk_index, chunk_text, embedding, model_name)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (document_id, chunk_index) DO UPDATE
                                SET chunk_text = EXCLUDED.chunk_text,
                                    embedding = EXCLUDED.embedding,
                                    model_name = EXCLUDED.model_name
                            """,
                            (
                                doc_id,
                                idx,
                                chunk,
                                embedding.tolist(),
                                EMBEDDING_MODEL_NAME
                            )
                        )
                        embedded_count += 1
            
            conn.commit()
    
    return embedded_count


if __name__ == "__main__":
    # For local development
    app.run(host="0.0.0.0", port=8000, debug=True)

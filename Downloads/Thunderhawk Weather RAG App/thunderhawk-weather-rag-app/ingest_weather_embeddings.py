"""Weather Data Sync and Embeddings Ingestion Pipeline.

Scheduled job that:
1. Fetches fresh weather alerts, forecasts, and discussions from NWS API
2. Stores weather documents in Lakebase (weather_documents table)
3. Chunks narrative text and generates embeddings using sentence-transformers
4. Stores embeddings in Lakebase (weather_embeddings table)

Designed to run as a Databricks Job on a recurring schedule.
Uses psycopg2 directly (no Spark) for reliable writes to Lakebase Postgres.

Usage:
    python ingest_weather_embeddings.py --locations "Chicago, IL" "Austin, TX" "TX" \
                                        --include-alerts --include-forecasts --include-discussions \
                                        --batch-size 50
"""

import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone

from psycopg2.extras import execute_values
from sentence_transformers import SentenceTransformer

import lakebase
from weather_client import (
    WeatherClient,
    parse_location,
    normalize_alert_to_document,
    normalize_forecast_discussion_to_document,
    normalize_forecast_period_to_document,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Chunking parameters
CHUNK_SIZE = 800  # Characters per chunk
CHUNK_OVERLAP = 100  # Overlap between chunks

# Embedding model
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384  # Dimension of all-MiniLM-L6-v2

# Table names
DOCUMENTS_TABLE = "weather_documents"
EMBEDDINGS_TABLE = "weather_embeddings"


def ensure_tables():
    """Create weather_documents and weather_embeddings tables if they don't exist."""
    logger.info("Ensuring database tables exist...")
    
    # Enable pgvector extension
    try:
        lakebase.run_write("CREATE EXTENSION IF NOT EXISTS vector")
        logger.info("✓ pgvector extension enabled")
    except Exception as e:
        logger.warning(f"Could not enable pgvector extension: {e}")
    
    # Create weather_documents table
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {DOCUMENTS_TABLE} (
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
    logger.info(f"✓ Created table: {DOCUMENTS_TABLE}")
    
    # Create indexes for weather_documents
    lakebase.run_write(
        f"CREATE INDEX IF NOT EXISTS idx_{DOCUMENTS_TABLE}_location "
        f"ON {DOCUMENTS_TABLE} (location)"
    )
    lakebase.run_write(
        f"CREATE INDEX IF NOT EXISTS idx_{DOCUMENTS_TABLE}_source_type "
        f"ON {DOCUMENTS_TABLE} (source_type)"
    )
    lakebase.run_write(
        f"CREATE INDEX IF NOT EXISTS idx_{DOCUMENTS_TABLE}_issued_at "
        f"ON {DOCUMENTS_TABLE} (issued_at DESC)"
    )
    logger.info(f"✓ Created indexes on {DOCUMENTS_TABLE}")
    
    # Create weather_embeddings table
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {EMBEDDINGS_TABLE} (
            id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            embedding vector({EMBEDDING_DIM}),
            model_name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(document_id, chunk_index)
        )
        """
    )
    logger.info(f"✓ Created table: {EMBEDDINGS_TABLE}")
    
    # Create indexes for weather_embeddings
    lakebase.run_write(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{EMBEDDINGS_TABLE}_embedding_hnsw
        ON {EMBEDDINGS_TABLE}
        USING hnsw (embedding vector_cosine_ops)
        """
    )
    logger.info("✓ Created HNSW index for vector similarity search")
    
    lakebase.run_write(
        f"CREATE INDEX IF NOT EXISTS idx_{EMBEDDINGS_TABLE}_document_id "
        f"ON {EMBEDDINGS_TABLE} (document_id)"
    )
    logger.info("✓ Created index on document_id")


def fetch_weather_data(
    client: WeatherClient,
    locations: list[str],
    include_alerts: bool = True,
    include_forecasts: bool = True,
    include_discussions: bool = True,
    limit: int = 50
) -> tuple[list[dict], list[dict]]:
    """
    Fetch weather data from NWS API for given locations.
    
    Returns:
        (documents, errors): List of document dicts and list of error dicts
    """
    all_documents = []
    all_errors = []
    
    for location_str in locations:
        try:
            # Parse location to lat/lon
            logger.info(f"Processing location: {location_str}")
            loc_info = parse_location(location_str)
            lat = loc_info["lat"]
            lon = loc_info["lon"]
            display_location = loc_info["display"]
            
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
                    all_errors.append({
                        "location": display_location,
                        "type": "alerts",
                        "error": str(e)
                    })
            
            # 2. Fetch forecast periods
            if include_forecasts:
                try:
                    forecast_periods = client.get_forecast(gridpoint)
                    for period in forecast_periods[:limit]:
                        doc = normalize_forecast_period_to_document(
                            period, display_location, office
                        )
                        documents.append(doc)
                    logger.info(f"Fetched {len(forecast_periods)} forecast periods for {display_location}")
                except Exception as e:
                    logger.warning(f"Failed to fetch forecasts for {display_location}: {e}")
                    all_errors.append({
                        "location": display_location,
                        "type": "forecasts",
                        "error": str(e)
                    })
            
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
                    all_errors.append({
                        "location": display_location,
                        "type": "discussion",
                        "error": str(e)
                    })
            
            all_documents.extend(documents)
            logger.info(f"✓ Collected {len(documents)} documents for {display_location}")
            
        except Exception as e:
            logger.error(f"Failed to process location {location_str}: {e}")
            all_errors.append({
                "location": location_str,
                "type": "general",
                "error": str(e)
            })
    
    return all_documents, all_errors


def upsert_weather_documents(documents: list[dict]) -> int:
    """Upsert weather documents into Lakebase using psycopg2."""
    if not documents:
        return 0
    
    count = 0
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            for doc in documents:
                cur.execute(
                    f"""
                    INSERT INTO {DOCUMENTS_TABLE} (
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


def generate_chunk_id(document_id: str, chunk_index: int) -> str:
    """Generate a stable, unique ID for a chunk."""
    id_string = f"{document_id}_chunk_{chunk_index}"
    return hashlib.md5(id_string.encode()).hexdigest()


def generate_embeddings(
    document_ids: list[str],
    model: SentenceTransformer
) -> int:
    """
    Generate embeddings for documents using psycopg2.
    
    Args:
        document_ids: List of document IDs to process
        model: Loaded SentenceTransformer model
    
    Returns:
        Number of embedding records created
    """
    if not document_ids:
        return 0
    
    embedded_count = 0
    
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            # Fetch documents that need embedding
            cur.execute(
                f"""
                SELECT id, narrative_text 
                FROM {DOCUMENTS_TABLE}
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
                    embeddings = model.encode(chunks, show_progress_bar=False)
                    
                    # Insert embeddings
                    for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
                        chunk_id = generate_chunk_id(doc_id, idx)
                        cur.execute(
                            f"""
                            INSERT INTO {EMBEDDINGS_TABLE} (
                                id, document_id, chunk_index, chunk_text,
                                embedding, model_name, created_at
                            )
                            VALUES (%s, %s, %s, %s, %s::vector, %s, %s)
                            ON CONFLICT (document_id, chunk_index) DO UPDATE
                                SET chunk_text = EXCLUDED.chunk_text,
                                    embedding = EXCLUDED.embedding,
                                    model_name = EXCLUDED.model_name,
                                    created_at = EXCLUDED.created_at
                            """,
                            (
                                chunk_id,
                                doc_id,
                                idx,
                                chunk,
                                embedding.tolist(),
                                EMBEDDING_MODEL,
                                datetime.now(timezone.utc)
                            )
                        )
                        embedded_count += 1
            
            conn.commit()
    
    return embedded_count


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Weather Data Sync and Embeddings Ingestion Pipeline"
    )
    parser.add_argument(
        "--locations",
        nargs="+",
        default=["Chicago, IL", "Austin, TX", "Miami, FL"],
        help="List of locations to fetch weather data for (e.g., 'Chicago, IL' 'TX' '41.88,-87.63')"
    )
    parser.add_argument(
        "--include-alerts",
        action="store_true",
        default=True,
        help="Include active weather alerts"
    )
    parser.add_argument(
        "--include-forecasts",
        action="store_true",
        default=True,
        help="Include forecast periods"
    )
    parser.add_argument(
        "--include-discussions",
        action="store_true",
        default=True,
        help="Include forecast discussions (AFD)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of items to fetch per location"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Batch size for processing documents"
    )
    
    return parser.parse_args()


def main():
    """Main pipeline execution."""
    args = parse_args()
    
    logger.info("=" * 60)
    logger.info("Weather Data Sync and Embeddings Ingestion Pipeline")
    logger.info("=" * 60)
    logger.info(f"Locations: {args.locations}")
    logger.info(f"Include alerts: {args.include_alerts}")
    logger.info(f"Include forecasts: {args.include_forecasts}")
    logger.info(f"Include discussions: {args.include_discussions}")
    logger.info(f"Limit per location: {args.limit}")
    logger.info("=" * 60)
    
    start_time = time.time()
    
    try:
        # Step 1: Ensure database tables exist
        logger.info("\n[Step 1/4] Ensuring database tables...")
        ensure_tables()
        
        # Step 2: Fetch weather data from NWS API
        logger.info("\n[Step 2/4] Fetching weather data from NWS API...")
        client = WeatherClient()
        documents, errors = fetch_weather_data(
            client,
            args.locations,
            include_alerts=args.include_alerts,
            include_forecasts=args.include_forecasts,
            include_discussions=args.include_discussions,
            limit=args.limit
        )
        
        logger.info(f"✓ Fetched {len(documents)} total documents")
        if errors:
            logger.warning(f"⚠ Encountered {len(errors)} errors:")
            for error in errors:
                logger.warning(f"  - {error['location']} ({error['type']}): {error['error']}")
        
        # Step 3: Upsert documents into Lakebase
        logger.info("\n[Step 3/4] Storing documents in Lakebase...")
        if documents:
            synced = upsert_weather_documents(documents)
            logger.info(f"✓ Synced {synced} documents to {DOCUMENTS_TABLE}")
        else:
            logger.warning("No documents to sync")
            return
        
        # Step 4: Generate and store embeddings
        logger.info("\n[Step 4/4] Generating embeddings...")
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        model = SentenceTransformer(EMBEDDING_MODEL)
        logger.info("✓ Model loaded")
        
        document_ids = [doc["id"] for doc in documents]
        embedded = generate_embeddings(document_ids, model)
        logger.info(f"✓ Generated {embedded} embeddings")
        
        # Summary
        elapsed = time.time() - start_time
        logger.info("\n" + "=" * 60)
        logger.info("Pipeline completed successfully!")
        logger.info(f"Total documents synced: {synced}")
        logger.info(f"Total embeddings generated: {embedded}")
        logger.info(f"Elapsed time: {elapsed:.2f}s")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.exception(f"Pipeline failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

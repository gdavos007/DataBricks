"""Database migrations for Weather RAG App.

Contains DDL for creating the weather_documents and weather_embeddings tables
in Lakebase Postgres with proper indexes and pgvector support.
"""

import logging

import lakebase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("migrations")

WEATHER_DOCUMENTS_TABLE = "weather_documents"
WEATHER_EMBEDDINGS_TABLE = "weather_embeddings"
EMBEDDING_DIM = 384  # Dimension of sentence-transformers/all-MiniLM-L6-v2


def create_weather_documents_table():
    """
    Create the weather_documents table for storing raw NWS data.
    
    Schema:
        - id: Unique identifier from NWS (alert URI or generated ID for forecasts)
        - location: Display location string (e.g., "Chicago, IL")
        - source_type: Type of document ("alert" or "forecast")
        - headline: Alert headline or forecast period name
        - event: Alert event type (e.g., "Tornado Warning")
        - narrative_text: Main text content for embedding (description, forecast narrative)
        - issued_at: When the document was issued by NWS
        - effective_at: When the alert/forecast becomes effective
        - payload: Full JSON response from NWS API
        - synced_at: When we fetched and stored this document
    """
    logger.info(f"Creating table: {WEATHER_DOCUMENTS_TABLE}")
    
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
    logger.info("✓ Table created successfully")


def create_weather_documents_indexes():
    """Create indexes on weather_documents for common query patterns."""
    logger.info("Creating indexes on weather_documents...")
    
    # Index for location-based queries
    lakebase.run_write(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{WEATHER_DOCUMENTS_TABLE}_location
        ON {WEATHER_DOCUMENTS_TABLE} (location)
        """
    )
    logger.info("✓ Created index on location")
    
    # Index for source_type filtering (alert vs forecast)
    lakebase.run_write(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{WEATHER_DOCUMENTS_TABLE}_source_type
        ON {WEATHER_DOCUMENTS_TABLE} (source_type)
        """
    )
    logger.info("✓ Created index on source_type")
    
    # Index for time-based queries (most recent first)
    lakebase.run_write(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{WEATHER_DOCUMENTS_TABLE}_issued_at
        ON {WEATHER_DOCUMENTS_TABLE} (issued_at DESC)
        """
    )
    logger.info("✓ Created index on issued_at")


def enable_pgvector_extension():
    """Enable the pgvector extension for vector similarity search."""
    logger.info("Enabling pgvector extension...")
    
    try:
        lakebase.run_write("CREATE EXTENSION IF NOT EXISTS vector")
        logger.info("✓ pgvector extension enabled")
    except Exception as e:
        logger.warning(f"Could not enable pgvector extension: {e}")
        logger.warning("If you see 'permission denied', contact your Lakebase admin.")
        raise


def create_weather_embeddings_table():
    """
    Create the weather_embeddings table for storing vector embeddings.
    
    Requires pgvector extension (call enable_pgvector_extension() first).
    
    Schema:
        - id: MD5 hash of document_id + chunk_index for stable IDs
        - document_id: Foreign key to weather_documents.id
        - chunk_index: Position of this chunk in the document (0-based)
        - chunk_text: The actual text chunk that was embedded
        - embedding: 384-dimensional vector from sentence-transformers model
        - model_name: Name of the embedding model used
        - created_at: When this embedding was generated
    """
    logger.info(f"Creating table: {WEATHER_EMBEDDINGS_TABLE}")
    
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {WEATHER_EMBEDDINGS_TABLE} (
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
    logger.info("✓ Table created successfully")


def create_weather_embeddings_indexes():
    """
    Create indexes on weather_embeddings for fast retrieval.
    
    - HNSW index: Fast approximate nearest neighbor search for similarity queries
    - B-tree index: Fast lookups by document_id for document-level operations
    """
    logger.info("Creating indexes on weather_embeddings...")
    
    # HNSW index for vector similarity search (cosine distance)
    logger.info("Creating HNSW index (this may take a while for large tables)...")
    lakebase.run_write(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{WEATHER_EMBEDDINGS_TABLE}_embedding_hnsw
        ON {WEATHER_EMBEDDINGS_TABLE}
        USING hnsw (embedding vector_cosine_ops)
        """
    )
    logger.info("✓ Created HNSW index for vector similarity search")
    
    # B-tree index for document_id lookups
    lakebase.run_write(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{WEATHER_EMBEDDINGS_TABLE}_document_id
        ON {WEATHER_EMBEDDINGS_TABLE} (document_id)
        """
    )
    logger.info("✓ Created index on document_id")


def run_all_migrations():
    """
    Run all migrations in order.
    
    This is idempotent - safe to run multiple times.
    """
    logger.info("="*60)
    logger.info("Running Weather RAG App Database Migrations")
    logger.info("="*60)
    logger.info()
    
    try:
        # Step 1: Create weather_documents table and indexes
        logger.info("[1/5] Creating weather_documents table...")
        create_weather_documents_table()
        
        logger.info("[2/5] Creating weather_documents indexes...")
        create_weather_documents_indexes()
        
        # Step 2: Enable pgvector for embeddings
        logger.info("[3/5] Enabling pgvector extension...")
        enable_pgvector_extension()
        
        # Step 3: Create weather_embeddings table and indexes
        logger.info("[4/5] Creating weather_embeddings table...")
        create_weather_embeddings_table()
        
        logger.info("[5/5] Creating weather_embeddings indexes...")
        create_weather_embeddings_indexes()
        
        logger.info()
        logger.info("="*60)
        logger.info("✓ All migrations completed successfully!")
        logger.info("="*60)
        logger.info()
        logger.info("Next steps:")
        logger.info("  1. Run: python app.py")
        logger.info("  2. Sync data: curl -X POST http://localhost:8080/weather/sync -H 'Content-Type: application/json' -d '{\"locations\": [\"Chicago, IL\"]}'")
        logger.info("  3. Generate embeddings: python ingest_weather_embeddings.py")
        logger.info("  4. Search: curl -X POST http://localhost:8080/weather/search -H 'Content-Type: application/json' -d '{\"query\": \"severe weather\"}'")
        logger.info()
        
    except Exception as e:
        logger.error("="*60)
        logger.error("✗ Migration failed!")
        logger.error(f"Error: {e}")
        logger.error("="*60)
        raise


def drop_all_tables():
    """
    Drop all tables (for cleanup/reset).
    
    WARNING: This deletes all data!
    """
    logger.warning("Dropping all tables...")
    
    # Drop in reverse order (embeddings first, then documents)
    lakebase.run_write(f"DROP TABLE IF EXISTS {WEATHER_EMBEDDINGS_TABLE} CASCADE")
    logger.info(f"✗ Dropped table: {WEATHER_EMBEDDINGS_TABLE}")
    
    lakebase.run_write(f"DROP TABLE IF EXISTS {WEATHER_DOCUMENTS_TABLE} CASCADE")
    logger.info(f"✗ Dropped table: {WEATHER_DOCUMENTS_TABLE}")
    
    logger.warning("✗ All tables dropped")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Run database migrations for Weather RAG App")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Drop all tables (WARNING: deletes all data)",
    )
    
    args = parser.parse_args()
    
    if args.drop:
        confirm = input("Are you sure you want to DROP ALL TABLES? This will delete all data. Type 'yes' to confirm: ")
        if confirm.lower() == "yes":
            drop_all_tables()
        else:
            logger.info("Aborted.")
    else:
        run_all_migrations()

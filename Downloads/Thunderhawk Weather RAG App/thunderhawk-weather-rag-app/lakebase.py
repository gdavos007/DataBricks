"""Lakebase (Databricks-managed Postgres) connection helper for Weather RAG App.

Connects using a LAKEBASE_URL stored in Databricks secrets.
"""

import base64
import os
from contextlib import contextmanager

import psycopg2
from databricks.sdk import WorkspaceClient
from psycopg2.extras import RealDictCursor
from sqlalchemy import create_engine

_w = WorkspaceClient()

_SCOPE = os.environ.get("LAKEBASE_SECRET_SCOPE", "weather-db")
_KEY = os.environ.get("LAKEBASE_SECRET_KEY", "lakebase-url")


def _lakebase_url() -> str:
    """Fetch and decode the Lakebase connection URL from the Databricks secret scope.
    
    Handles multiple formats:
    - Base64-encoded connection strings
    - Plain Postgres URIs (postgresql:// or postgres://)
    - Double-encoded strings
    """
    secret = _w.secrets.get_secret(scope=_SCOPE, key=_KEY)
    conn_str = secret.value
    
    # Check if already a valid Postgres URI
    if conn_str.startswith('postgresql://') or conn_str.startswith('postgres://'):
        return conn_str
    
    # Try Base64 decoding
    try:
        decoded = base64.b64decode(conn_str).decode('utf-8')
        
        # Check if decoded value is a valid URI
        if decoded.startswith('postgresql://') or decoded.startswith('postgres://'):
            return decoded
        
        # Check if it needs another round of decoding (double-encoded)
        if not ('=' in decoded and 'postgresql' not in decoded):
            try:
                double_decoded = base64.b64decode(decoded).decode('utf-8')
                if double_decoded.startswith('postgresql://') or double_decoded.startswith('postgres://'):
                    return double_decoded
            except Exception:
                pass
        
        return decoded
    except Exception as e:
        # If Base64 decode fails, return original
        return conn_str


@contextmanager
def get_connection():
    """Yield a raw psycopg2 connection with a RealDictCursor factory."""
    conn = psycopg2.connect(_lakebase_url(), cursor_factory=RealDictCursor)
    try:
        yield conn
    finally:
        conn.close()


def get_engine():
    """Return a SQLAlchemy engine for Lakebase.
    
    Attempts connection in this order:
    1. Lakebase secret URL (primary)
    2. DATABASE_URL or LAKEBASE_CONNECTION_STRING env var
    3. Constructs URI from individual PG* environment variables
    """
    try:
        conn_str = _lakebase_url()
        return create_engine(conn_str)
    except Exception:
        # Fallback to environment variables
        database_url = os.environ.get('DATABASE_URL') or os.environ.get('LAKEBASE_CONNECTION_STRING')
        
        if database_url:
            # Check if needs decoding
            if not (database_url.startswith('postgresql://') or database_url.startswith('postgres://')):
                try:
                    database_url = base64.b64decode(database_url).decode('utf-8')
                except Exception:
                    pass
            
            return create_engine(database_url)
        else:
            # Construct URI from individual PG* environment variables
            host = os.environ.get('PGHOST')
            port = os.environ.get('PGPORT', '5432')
            database = os.environ.get('PGDATABASE')
            user = os.environ.get('PGUSER')
            password = os.environ.get('PGPASSWORD')
            
            if host and database and user:
                uri = f'postgresql://{user}:{password}@{host}:{port}/{database}'
                return create_engine(uri)
            else:
                raise ValueError('No valid database connection configuration found')


def run_query(sql: str, params: tuple | dict | None = None) -> list[dict]:
    """Run a read query against Lakebase and return rows as list[dict]."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def run_write(sql: str, params: tuple | dict | None = None) -> int:
    """Run an INSERT/UPDATE/DELETE against Lakebase, return affected row count."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount


def init_db():
    """Initialize database schema: enable vector extension and create tables.
    
    Creates:
    - pgvector extension
    - weather_documents table
    - weather_embeddings table with vector column
    - HNSW index for fast vector similarity search
    """
    import logging
    logger = logging.getLogger(__name__)
    
    with get_connection() as conn:
        with conn.cursor() as cur:
            # Enable pgvector extension
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                conn.commit()
                logger.info("✓ pgvector extension enabled")
            except Exception as e:
                logger.warning(f"Could not enable pgvector extension: {e}")
            
            # Create weather_documents table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS weather_documents (
                    id TEXT PRIMARY KEY,
                    location TEXT,
                    source_type TEXT,
                    headline TEXT,
                    event TEXT,
                    narrative_text TEXT,
                    issued_at TIMESTAMPTZ,
                    effective_at TIMESTAMPTZ,
                    payload JSONB,
                    synced_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            conn.commit()
            logger.info("✓ Created table: weather_documents")
            
            # Create indexes on weather_documents
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_weather_documents_location 
                ON weather_documents(location)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_weather_documents_source_type 
                ON weather_documents(source_type)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_weather_documents_issued_at 
                ON weather_documents(issued_at DESC)
            """)
            conn.commit()
            logger.info("✓ Created indexes on weather_documents")
            
            # Create weather_embeddings table
            cur.execute("""
                CREATE TABLE IF NOT EXISTS weather_embeddings (
                    id SERIAL PRIMARY KEY,
                    document_id TEXT REFERENCES weather_documents(id) ON DELETE CASCADE,
                    chunk_index INT,
                    chunk_text TEXT,
                    embedding vector(384),
                    model_name TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE(document_id, chunk_index)
                )
            """)
            conn.commit()
            logger.info("✓ Created table: weather_embeddings")
            
            # Create HNSW index for vector similarity search
            cur.execute("""
                CREATE INDEX IF NOT EXISTS weather_embeddings_hnsw 
                ON weather_embeddings 
                USING hnsw (embedding vector_cosine_ops)
            """)
            conn.commit()
            logger.info("✓ Created HNSW index for vector similarity search")
            
            # Create index on document_id for lookups
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_weather_embeddings_document_id 
                ON weather_embeddings(document_id)
            """)
            conn.commit()
            logger.info("✓ Created index on document_id")

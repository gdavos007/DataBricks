# Databricks notebook source
# DBTITLE 1,Weather Embeddings Pipeline
# MAGIC %md
# MAGIC

# COMMAND ----------

# DBTITLE 1,Install Dependencies
# MAGIC %undefined
# MAGIC pip install sentence-transformers psycopg2-binary

# COMMAND ----------

# DBTITLE 1,Configuration
# Configuration
CHUNK_SIZE = 800  # Characters per chunk
CHUNK_OVERLAP = 100  # Overlap between chunks

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384  # Dimension of all-MiniLM-L6-v2

DOCUMENTS_TABLE = "weather_documents"
EMBEDDINGS_TABLE = "weather_embeddings"

BATCH_SIZE = 50  # Documents to process per batch

print(f"Chunk size: {CHUNK_SIZE}")
print(f"Chunk overlap: {CHUNK_OVERLAP}")
print(f"Embedding model: {EMBEDDING_MODEL}")
print(f"Embedding dimension: {EMBEDDING_DIM}")

# COMMAND ----------

# DBTITLE 1,Create Embeddings Table with pgvector
import lakebase

# Enable pgvector extension
try:
    lakebase.run_write("CREATE EXTENSION IF NOT EXISTS vector")
    print("✓ pgvector extension enabled")
except Exception as e:
    print(f"Warning: {e}")

# Create embeddings table with vector column
lakebase.run_write(f"""
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
""")
print(f"✓ Created table: {EMBEDDINGS_TABLE}")

# Create HNSW index for fast vector similarity search
lakebase.run_write(f"""
    CREATE INDEX IF NOT EXISTS idx_{EMBEDDINGS_TABLE}_embedding_hnsw
    ON {EMBEDDINGS_TABLE}
    USING hnsw (embedding vector_cosine_ops)
""")
print("✓ Created HNSW index for cosine similarity")

# Create B-tree index for document lookups
lakebase.run_write(f"""
    CREATE INDEX IF NOT EXISTS idx_{EMBEDDINGS_TABLE}_document_id 
    ON {EMBEDDINGS_TABLE} (document_id)
""")
print("✓ Created index on document_id")

print("\n✓ Table setup complete!")

# COMMAND ----------

# DBTITLE 1,Chunking Function
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """
    Split text into overlapping chunks using a sliding window.
    """
    if not text or len(text) <= chunk_size:
        return [text] if text else []
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        chunks.append(chunk)
        
        # Move forward with overlap
        start = end - overlap
        
        if start >= len(text):
            break
    
    return chunks

# Test chunking
test_text = "This is a test. " * 100  # ~1600 chars
test_chunks = chunk_text(test_text)
print(f"Test text length: {len(test_text)} chars")
print(f"Number of chunks: {len(test_chunks)}")
print(f"First chunk length: {len(test_chunks[0])} chars")
print(f"Last chunk length: {len(test_chunks[-1])} chars")

# COMMAND ----------

# DBTITLE 1,Load Embedding Model
from sentence_transformers import SentenceTransformer
import time

print(f"Loading model: {EMBEDDING_MODEL}...")
start = time.time()
model = SentenceTransformer(EMBEDDING_MODEL)
load_time = time.time() - start

print(f"✓ Model loaded in {load_time:.2f}s")
print(f"Model dimension: {model.get_sentence_embedding_dimension()}")

# Test encoding
test_sentences = ["Weather alert", "Forecast discussion"]
test_embeddings = model.encode(test_sentences)
print(f"\nTest encoding:")
print(f"  Input: {len(test_sentences)} sentences")
print(f"  Output shape: {test_embeddings.shape}")

# COMMAND ----------

# DBTITLE 1,Fetch Unembedded Documents
import lakebase

def get_unembedded_documents(batch_size: int = BATCH_SIZE) -> list[dict]:
    """
    Fetch documents that don't have embeddings yet.
    """
    query = f"""
        SELECT 
            wd.id,
            wd.location,
            wd.source_type,
            wd.event,
            wd.headline,
            wd.narrative_text,
            wd.issued_at,
            wd.synced_at
        FROM {DOCUMENTS_TABLE} wd
        LEFT JOIN {EMBEDDINGS_TABLE} we ON wd.id = we.document_id
        WHERE we.document_id IS NULL
           OR wd.synced_at > we.created_at
        ORDER BY wd.synced_at DESC
        LIMIT %s
    """
    return lakebase.run_query(query, (batch_size,))

# Fetch a batch
documents = get_unembedded_documents()
print(f"Found {len(documents)} unembedded documents")

if documents:
    sample = documents[0]
    print(f"\nSample document:")
    print(f"  ID: {sample['id']}")
    print(f"  Location: {sample['location']}")
    print(f"  Type: {sample['source_type']}")
    print(f"  Event: {sample['event']}")
    print(f"  Text length: {len(sample['narrative_text'] or '')} chars")

# COMMAND ----------

# DBTITLE 1,Generate Embeddings for Documents
import hashlib
from datetime import datetime, timezone

def generate_chunk_id(document_id: str, chunk_index: int) -> str:
    """Generate a stable, unique ID for a chunk."""
    id_string = f"{document_id}_chunk_{chunk_index}"
    return hashlib.md5(id_string.encode()).hexdigest()

def embed_documents(documents: list[dict]) -> list[tuple]:
    """
    Chunk and embed documents.
    Returns list of tuples: (id, document_id, chunk_index, chunk_text, embedding_list, model_name, created_at)
    """
    embedding_rows = []
    
    for doc in documents:
        document_id = doc["id"]
        narrative_text = doc.get("narrative_text") or ""
        
        if not narrative_text.strip():
            print(f"Skipping {document_id}: no text")
            continue
        
        # Chunk the text
        chunks = chunk_text(narrative_text)
        
        # Generate embeddings for all chunks (batch)
        chunk_embeddings = model.encode(chunks, show_progress_bar=False)
        
        # Create rows
        created_at = datetime.now(timezone.utc)
        for chunk_index, (chunk_text, embedding) in enumerate(zip(chunks, chunk_embeddings)):
            chunk_id = generate_chunk_id(document_id, chunk_index)
            embedding_list = embedding.tolist()
            
            embedding_rows.append((
                chunk_id,
                document_id,
                chunk_index,
                chunk_text,
                embedding_list,
                EMBEDDING_MODEL,
                created_at,
            ))
    
    return embedding_rows

# Generate embeddings for the fetched documents
if documents:
    print(f"Generating embeddings for {len(documents)} documents...")
    start = time.time()
    embedding_rows = embed_documents(documents)
    elapsed = time.time() - start
    
    print(f"✓ Generated {len(embedding_rows)} chunk embeddings in {elapsed:.2f}s")
    print(f"  Avg: {elapsed/len(embedding_rows)*1000:.1f}ms per chunk")
else:
    embedding_rows = []
    print("No documents to embed")

# COMMAND ----------

# DBTITLE 1,Write Embeddings to Lakebase
from psycopg2.extras import execute_values
import lakebase

def write_embeddings_batch(embedding_rows: list[tuple]) -> int:
    """
    Write embeddings using execute_values for efficient batch insert.
    """
    if not embedding_rows:
        return 0
    
    insert_sql = f"""
        INSERT INTO {EMBEDDINGS_TABLE} (
            id, document_id, chunk_index, chunk_text, embedding, model_name, created_at
        )
        VALUES %s
        ON CONFLICT (document_id, chunk_index) DO UPDATE
            SET chunk_text = EXCLUDED.chunk_text,
                embedding = EXCLUDED.embedding,
                model_name = EXCLUDED.model_name,
                created_at = EXCLUDED.created_at
    """
    
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            # Cast embedding list to vector(384) using template
            execute_values(
                cur,
                insert_sql,
                embedding_rows,
                template="(%s, %s, %s, %s, %s::vector, %s, %s)",  # Cast to vector
                page_size=100,
            )
            conn.commit()
    
    return len(embedding_rows)

# Write the embeddings
if embedding_rows:
    print(f"Writing {len(embedding_rows)} embeddings to Lakebase...")
    start = time.time()
    written = write_embeddings_batch(embedding_rows)
    elapsed = time.time() - start
    
    print(f"✓ Wrote {written} embeddings in {elapsed:.2f}s")
    print(f"  Throughput: {written/elapsed:.1f} rows/sec")
else:
    print("No embeddings to write")

# COMMAND ----------

# DBTITLE 1,Verify Embeddings
# Query embeddings table
rows = lakebase.run_query(f"""
    SELECT 
        document_id,
        chunk_index,
        LEFT(chunk_text, 100) as chunk_preview,
        model_name,
        created_at
    FROM {EMBEDDINGS_TABLE}
    ORDER BY created_at DESC
    LIMIT 10
""")

print(f"Total embeddings in table: {len(rows)}")
print("\nRecent embeddings:")
for row in rows[:5]:
    print(f"  Doc: {row['document_id'][:16]}... Chunk {row['chunk_index']}")
    print(f"    Text: {row['chunk_preview']}...")
    print(f"    Model: {row['model_name']}")
    print()

# COMMAND ----------

# DBTITLE 1,Test Vector Similarity Search
# Test similarity search
query_text = "severe weather warning"
print(f"Query: '{query_text}'")

# Embed the query
query_embedding = model.encode([query_text])[0].tolist()

# Find similar chunks using cosine similarity
similar_chunks = lakebase.run_query(
    f"""
    SELECT 
        document_id,
        chunk_index,
        LEFT(chunk_text, 150) as chunk_preview,
        1 - (embedding <=> %s::vector) as similarity
    FROM {EMBEDDINGS_TABLE}
    ORDER BY embedding <=> %s::vector
    LIMIT 5
    """,
    (query_embedding, query_embedding)
)

print(f"\nTop {len(similar_chunks)} similar chunks:\n")
for i, chunk in enumerate(similar_chunks, 1):
    print(f"{i}. Similarity: {chunk['similarity']:.4f}")
    print(f"   Document: {chunk['document_id'][:24]}... Chunk {chunk['chunk_index']}")
    print(f"   Text: {chunk['chunk_preview']}...")
    print()

# COMMAND ----------

# DBTITLE 1,Complete Pipeline (Run All Batches)
def run_complete_pipeline(max_batches: int = None):
    """
    Process all unembedded documents in batches.
    """
    total_docs = 0
    total_chunks = 0
    batch_num = 0
    
    print("Starting complete embedding pipeline...\n")
    
    while True:
        batch_num += 1
        
        if max_batches and batch_num > max_batches:
            print(f"Reached max batches: {max_batches}")
            break
        
        print(f"--- Batch {batch_num} ---")
        
        # Fetch documents
        docs = get_unembedded_documents(BATCH_SIZE)
        
        if not docs:
            print("No more documents to process")
            break
        
        print(f"Processing {len(docs)} documents...")
        
        # Generate embeddings
        start = time.time()
        rows = embed_documents(docs)
        embed_time = time.time() - start
        
        # Write to database
        start = time.time()
        written = write_embeddings_batch(rows)
        write_time = time.time() - start
        
        print(f"  Embedded: {len(rows)} chunks in {embed_time:.2f}s")
        print(f"  Written: {written} rows in {write_time:.2f}s")
        
        total_docs += len(docs)
        total_chunks += written
        
        if len(docs) < BATCH_SIZE:
            break
    
    print(f"\n{'='*60}")
    print(f"Pipeline Complete!")
    print(f"  Total documents: {total_docs}")
    print(f"  Total chunks: {total_chunks}")
    print(f"{'='*60}")

# Run the complete pipeline
# Uncomment to process all documents:
# run_complete_pipeline()

# Or process a limited number of batches:
run_complete_pipeline(max_batches=3)
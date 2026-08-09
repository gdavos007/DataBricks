# Databricks notebook source
# DBTITLE 1,Setup Instructions
# MAGIC %md
# MAGIC

# COMMAND ----------

# DBTITLE 1,1. Store Database Credentials
# Run this cell to store your Lakebase connection URL securely
import base64
from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

LAKEBASE_URL = "postgresql://student:npg_1igvhNWJru9y@ep-super-flower-d86lslmj.database.us-east-2.cloud.databricks.com/databricks_postgres?sslmode=require"
SCOPE = "weather-db"
KEY = "lakebase-url"

# Create secret scope
try:
    w.secrets.create_scope(scope=SCOPE)
    print(f"✓ Created secret scope: {SCOPE}")
except Exception as e:
    if "already exists" in str(e).lower():
        print(f"✓ Secret scope already exists: {SCOPE}")
    else:
        print(f"✗ Error: {e}")

# Store the connection URL
encoded_url = base64.b64encode(LAKEBASE_URL.encode()).decode()
try:
    w.secrets.put_secret(scope=SCOPE, key=KEY, string_value=encoded_url)
    print(f"✓ Stored secret: {SCOPE}/{KEY}")
    print("\n✓ Setup complete!")
except Exception as e:
    print(f"✗ Error: {e}")

# COMMAND ----------

# DBTITLE 1,2. Test Weather Client
from weather_client import WeatherClient, parse_location

client = WeatherClient()

# Test location parsing
print("Testing location parsing...")
chicago = parse_location("Chicago, IL")
print(f"  Chicago: lat={chicago['lat']}, lon={chicago['lon']}")

# Resolve to NWS grid point
print("\nResolving to grid point...")
gridpoint = client.resolve_location_to_gridpoint(chicago['lat'], chicago['lon'])
print(f"  Office: {gridpoint['office']}")
print(f"  Grid: ({gridpoint['gridX']}, {gridpoint['gridY']})")

# Fetch alerts
print("\nFetching active alerts...")
alerts = client.get_active_alerts(lat=chicago['lat'], lon=chicago['lon'], limit=3)
print(f"  Found {len(alerts)} active alerts")

print("\n✓ Weather client working!")

# COMMAND ----------

# DBTITLE 1,3. Create Weather Table
import lakebase

# Create the weather_documents table
lakebase.run_write("""
    CREATE TABLE IF NOT EXISTS weather_documents (
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
""")

# Create indexes
lakebase.run_write("CREATE INDEX IF NOT EXISTS idx_weather_location ON weather_documents (location)")
lakebase.run_write("CREATE INDEX IF NOT EXISTS idx_weather_source_type ON weather_documents (source_type)")
lakebase.run_write("CREATE INDEX IF NOT EXISTS idx_weather_issued_at ON weather_documents (issued_at DESC)")

print("✓ Table created successfully!")

# Check table exists
rows = lakebase.run_query("SELECT COUNT(*) as count FROM weather_documents")
print(f"\nCurrent document count: {rows[0]['count']}")

# COMMAND ----------

# DBTITLE 1,4. Test Sync Functionality
import json
from weather_client import (
    WeatherClient,
    parse_location,
    normalize_alert_to_document,
    normalize_forecast_discussion_to_document,
    normalize_forecast_period_to_document,
)
import lakebase

client = WeatherClient()

# Test syncing data for Chicago
print("Syncing weather data for Chicago, IL...\n")

location_str = "Chicago, IL"
loc_info = parse_location(location_str)
lat, lon = loc_info['lat'], loc_info['lon']
display = loc_info['display']

# Get grid point
gridpoint = client.resolve_location_to_gridpoint(lat, lon)
office = gridpoint['office']

documents = []

# Fetch alerts
print("Fetching alerts...")
alerts = client.get_active_alerts(lat=lat, lon=lon, limit=10)
for alert in alerts:
    doc = normalize_alert_to_document(alert, display)
    documents.append(doc)
print(f"  ✓ {len(alerts)} alerts")

# Fetch forecasts
print("Fetching forecasts...")
forecasts = client.get_forecast(gridpoint)
for period in forecasts[:7]:  # Next 7 periods
    doc = normalize_forecast_period_to_document(period, display, office)
    documents.append(doc)
print(f"  ✓ {len(forecasts[:7])} forecast periods")

# Fetch discussion
print("Fetching forecast discussion...")
discussion = client.get_forecast_discussion(office)
if discussion:
    doc = normalize_forecast_discussion_to_document(discussion, display, office)
    documents.append(doc)
    print(f"  ✓ 1 forecast discussion")

print(f"\nTotal documents: {len(documents)}")

# Upsert documents
if documents:
    count = 0
    with lakebase.get_connection() as conn:
        with conn.cursor() as cur:
            for doc in documents:
                cur.execute("""
                    INSERT INTO weather_documents (
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
                """, (
                    doc['id'], doc['location'], doc['source_type'],
                    doc.get('headline'), doc.get('event'), doc.get('narrative_text'),
                    doc.get('issued_at'), doc.get('effective_at'),
                    json.dumps(doc.get('payload', {})), doc['synced_at']
                ))
                count += 1
            conn.commit()
    
    print(f"\n✓ Synced {count} documents to Lakebase!")

# COMMAND ----------

# DBTITLE 1,5. Query Synced Data
import lakebase

# Query recent documents
print("Recent weather documents:\n")
rows = lakebase.run_query("""
    SELECT id, location, source_type, event, headline, issued_at
    FROM weather_documents
    ORDER BY synced_at DESC
    LIMIT 10
""")

for row in rows:
    print(f"[{row['source_type'].upper()}] {row['location']}")
    print(f"  Event: {row['event']}")
    print(f"  {row['headline'][:100]}..." if len(row['headline']) > 100 else f"  {row['headline']}")
    print(f"  Issued: {row['issued_at']}")
    print()

print(f"\nTotal documents synced: {len(rows)}")
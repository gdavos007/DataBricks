"""Test script for the Weather RAG App /weather/sync endpoint.

Tests syncing weather data for multiple locations.
"""

import json
import requests

# Flask app URL (change if running on different host/port)
BASE_URL = "http://localhost:8080"


def test_health_check():
    """Test the health check endpoint."""
    print("Testing health check...")
    resp = requests.get(f"{BASE_URL}/healthz")
    resp.raise_for_status()
    print(f"✓ Health check passed: {resp.json()}")
    print()


def test_weather_sync():
    """Test syncing weather data for multiple locations."""
    print("Testing weather sync endpoint...")
    
    payload = {
        "locations": [
            "Chicago, IL",
            "Austin, TX",
            "San Francisco, CA"
        ],
        "limit": 50,
        "include_alerts": True,
        "include_forecasts": True,
        "include_discussions": True
    }
    
    print(f"Request payload:")
    print(json.dumps(payload, indent=2))
    print()
    
    resp = requests.post(
        f"{BASE_URL}/weather/sync",
        json=payload,
        headers={"Content-Type": "application/json"}
    )
    
    resp.raise_for_status()
    result = resp.json()
    
    print(f"✓ Sync completed successfully!")
    print(f"  Synced documents: {result['synced']}")
    print(f"  Locations processed: {result['locations']}")
    if result.get('errors'):
        print(f"  Errors: {result['errors']}")
    print()
    
    return result


def test_list_documents():
    """Test listing synced weather documents."""
    print("Testing document listing...")
    
    resp = requests.get(f"{BASE_URL}/weather/documents?limit=5")
    resp.raise_for_status()
    docs = resp.json()
    
    print(f"✓ Retrieved {len(docs)} documents")
    
    if docs:
        print("\nSample document:")
        doc = docs[0]
        print(f"  ID: {doc['id']}")
        print(f"  Location: {doc['location']}")
        print(f"  Type: {doc['source_type']}")
        print(f"  Event: {doc['event']}")
        print(f"  Headline: {doc['headline'][:80]}..." if len(doc['headline']) > 80 else f"  Headline: {doc['headline']}")
        print(f"  Issued: {doc['issued_at']}")
        print(f"  Synced: {doc['synced_at']}")
    print()


def test_filter_by_source_type():
    """Test filtering documents by source type."""
    print("Testing source type filtering...")
    
    # Get alerts only
    resp = requests.get(f"{BASE_URL}/weather/documents?source_type=alert&limit=3")
    resp.raise_for_status()
    alerts = resp.json()
    print(f"✓ Retrieved {len(alerts)} alerts")
    
    # Get forecasts only
    resp = requests.get(f"{BASE_URL}/weather/documents?source_type=forecast&limit=3")
    resp.raise_for_status()
    forecasts = resp.json()
    print(f"✓ Retrieved {len(forecasts)} forecasts")
    print()


def main():
    """Run all tests."""
    print("="*60)
    print("Weather RAG App - Endpoint Tests")
    print("="*60)
    print()
    
    try:
        test_health_check()
        test_weather_sync()
        test_list_documents()
        test_filter_by_source_type()
        
        print("="*60)
        print("✓ All tests passed!")
        print("="*60)
        
    except requests.exceptions.ConnectionError:
        print("✗ Failed to connect to the Flask app.")
        print(f"   Make sure the app is running on {BASE_URL}")
        print("   Run: python app.py")
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

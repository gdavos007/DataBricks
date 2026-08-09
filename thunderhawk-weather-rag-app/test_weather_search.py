#!/usr/bin/env python3
"""Test the /weather/search endpoint of the Weather RAG App.

Run the Flask app first:
    python app.py

Then run this test:
    python test_weather_search.py
"""

import json
import sys

import requests

BASE_URL = "http://localhost:8080"


def test_search(query: str, top_k: int = 5):
    """Test semantic search endpoint."""
    print(f"\n{'='*60}")
    print(f"Testing semantic search: '{query}'")
    print(f"Top K: {top_k}")
    print(f"{'='*60}\n")

    response = requests.post(
        f"{BASE_URL}/weather/search",
        json={"query": query, "top_k": top_k},
        headers={"Content-Type": "application/json"},
    )

    print(f"Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        results = data.get("results", [])
        total_embeddings = data.get("total_embeddings", 0)

        print(f"✓ Search successful!")
        print(f"  Total embeddings in database: {total_embeddings}")
        print(f"  Results returned: {len(results)}")

        if results:
            print(f"\nTop {len(results)} matches:\n")
            for i, result in enumerate(results, 1):
                print(f"{i}. Similarity: {result['similarity']:.4f}")
                print(f"   Location: {result['location']}")
                print(f"   Headline: {result['headline'] or 'N/A'}")
                print(f"   Chunk: {result['chunk_text'][:150]}...")
                print()
        else:
            message = data.get("message")
            if message:
                print(f"\n⚠️  {message}")
            else:
                print("\nNo results found.")
    else:
        print(f"✗ Error: {response.status_code}")
        try:
            error = response.json()
            print(f"  {json.dumps(error, indent=2)}")
        except:
            print(f"  {response.text}")

    return response.status_code == 200


def test_edge_cases():
    """Test error handling."""
    print(f"\n{'='*60}")
    print("Testing edge cases...")
    print(f"{'='*60}\n")

    tests = [
        ("Empty query", {"query": "", "top_k": 5}),
        ("Missing query", {"top_k": 5}),
        ("Invalid top_k (string)", {"query": "test", "top_k": "abc"}),
        ("Invalid top_k (negative)", {"query": "test", "top_k": -1}),
        ("Invalid top_k (too large)", {"query": "test", "top_k": 100}),
    ]

    for test_name, body in tests:
        print(f"Test: {test_name}")
        response = requests.post(
            f"{BASE_URL}/weather/search",
            json=body,
            headers={"Content-Type": "application/json"},
        )
        print(f"  Status: {response.status_code}")

        if response.status_code != 200:
            try:
                error = response.json()
                print(f"  Error: {error.get('error')}")
            except:
                print(f"  Response: {response.text[:100]}")
        print()


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("Weather RAG App - Search Endpoint Tests")
    print("="*60)

    # Check if app is running
    try:
        response = requests.get(f"{BASE_URL}/healthz", timeout=2)
        if response.status_code != 200:
            print("✗ App is not responding. Start the Flask app first:")
            print("  python app.py")
            sys.exit(1)
    except requests.exceptions.RequestException:
        print("✗ Cannot connect to app. Start the Flask app first:")
        print("  python app.py")
        sys.exit(1)

    print("✓ App is running\n")

    # Test various queries
    queries = [
        "severe weather warnings",
        "flooding near rivers",
        "high winds and storms",
        "temperature forecast",
        "winter weather advisory",
    ]

    success_count = 0
    for query in queries:
        if test_search(query, top_k=3):
            success_count += 1

    # Test edge cases
    test_edge_cases()

    print(f"\n{'='*60}")
    print(f"Tests completed: {success_count}/{len(queries)} successful searches")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()

"""Client for the National Weather Service (NWS) API.

Fetches weather alerts and forecast discussions for given locations.
The NWS API is public and requires no authentication.

API docs: https://www.weather.gov/documentation/services-web-api
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

import requests


def parse_timestamp(timestamp_str: str | None) -> datetime | None:
    """
    Parse an ISO 8601 timestamp string to a datetime object.
    Returns None if the input is None, empty string, or invalid.
    
    Args:
        timestamp_str: ISO 8601 timestamp string (e.g. "2024-01-15T12:00:00-06:00")
    
    Returns:
        datetime object with timezone, or None
    """
    if not timestamp_str or not isinstance(timestamp_str, str):
        return None
    
    timestamp_str = timestamp_str.strip()
    if not timestamp_str:
        return None
    
    try:
        # Try parsing with fromisoformat (handles most ISO 8601 formats)
        return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        # Invalid or malformed timestamp
        return None

_BASE_URL = "https://api.weather.gov"
_DEFAULT_TIMEOUT = 30

# NWS API requires a User-Agent header
_HEADERS = {
    "User-Agent": "(Databricks Weather RAG App, contact@example.com)",
    "Accept": "application/geo+json",
}


class WeatherClient:
    """Thin wrapper around the NWS API for fetching alerts and forecasts."""

    def __init__(self, base_url: str | None = None, timeout: int = _DEFAULT_TIMEOUT):
        self.base_url = (base_url or _BASE_URL).rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(_HEADERS)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to the NWS API."""
        resp = self._session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def resolve_location_to_gridpoint(self, lat: float, lon: float) -> dict:
        """
        Resolve a lat/lon to an NWS grid point.
        Returns: {"office": "ABC", "gridX": 123, "gridY": 456, "forecast": "url", ...}
        """
        data = self.get(f"/points/{lat},{lon}")
        properties = data.get("properties", {})
        return {
            "office": properties.get("gridId"),
            "gridX": properties.get("gridX"),
            "gridY": properties.get("gridY"),
            "forecast": properties.get("forecast"),
            "forecast_hourly": properties.get("forecastHourly"),
            "forecast_office": properties.get("forecastOffice"),
        }

    def get_active_alerts(self, lat: float | None = None, lon: float | None = None, area: str | None = None, limit: int = 50) -> list[dict]:
        """
        Fetch active weather alerts.
        If lat/lon are provided, filters to alerts affecting that point.
        If area (state code like "TX") is provided, filters to that state.
        Returns the 'features' list from the NWS alerts response.
        
        Note: NWS API does not support 'limit' or 'status' query parameters.
        Use point= or area= to filter results.
        """
        params: dict[str, Any] = {}
        
        if lat is not None and lon is not None:
            params["point"] = f"{lat},{lon}"
        elif area:
            params["area"] = area.upper()
        
        data = self.get("/alerts/active", params=params)
        features = data.get("features", [])
        
        # Apply limit client-side since API doesn't support it
        return features[:limit] if limit else features

    def get_forecast_discussion(self, office: str) -> dict | None:
        """
        Fetch the latest Area Forecast Discussion (AFD) for a given NWS office.
        AFD contains detailed narrative forecasts written by meteorologists.
        Returns the most recent product, or None if unavailable.
        """
        try:
            # Get list of AFD products for this office
            data = self.get(f"/products/types/AFD/locations/{office}")
            products = data.get("@graph", [])
            
            if not products:
                return None
            
            # Get the most recent product (first in list)
            product_url = products[0].get("@id")
            if not product_url:
                return None
            
            # Fetch the full product details
            # Extract path from the full URL
            path = product_url.replace(self.base_url, "").replace("https://api.weather.gov", "")
            return self.get(path)
        except (requests.HTTPError, KeyError, IndexError):
            # Office might not have AFD products, or they might be unavailable
            return None

    def get_forecast(self, gridpoint_data: dict) -> list[dict]:
        """
        Fetch the forecast for a given gridpoint.
        Returns the 'periods' list from the forecast response.
        """
        forecast_url = gridpoint_data.get("forecast")
        if not forecast_url:
            return []
        
        # Extract path from the full URL
        path = forecast_url.replace(self.base_url, "").replace("https://api.weather.gov", "")
        data = self.get(path)
        properties = data.get("properties", {})
        return properties.get("periods", [])


def normalize_alert_to_document(alert_feature: dict, location: str) -> dict:
    """
    Normalize an NWS alert feature into a document record.
    
    Args:
        alert_feature: A single 'feature' from the NWS alerts response
        location: Human-readable location string (e.g. "Chicago, IL" or "41.88,-87.63")
    
    Returns:
        Document dict with: id, location, source_type, headline, event, 
        narrative_text, issued_at, effective_at, payload, synced_at
    """
    props = alert_feature.get("properties", {})
    
    # Use the NWS alert ID as our document ID
    alert_id = props.get("id") or alert_feature.get("id")
    
    # Build narrative from description + instruction
    description = props.get("description", "")
    instruction = props.get("instruction", "")
    narrative_parts = [p for p in [description, instruction] if p]
    narrative_text = "\n\n".join(narrative_parts)
    
    return {
        "id": alert_id,
        "location": location,
        "source_type": "alert",
        "headline": props.get("headline", ""),
        "event": props.get("event", ""),
        "narrative_text": narrative_text,
        "issued_at": parse_timestamp(props.get("sent")),
        "effective_at": parse_timestamp(props.get("effective")),
        "payload": alert_feature,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


def normalize_forecast_discussion_to_document(product: dict, location: str, office: str) -> dict:
    """
    Normalize an NWS Area Forecast Discussion (AFD) product into a document record.
    
    Args:
        product: The AFD product from the NWS API
        location: Human-readable location string
        office: NWS office code (e.g. "LOT" for Chicago)
    
    Returns:
        Document dict with all required fields
    """
    props = product.get("properties", {}) if isinstance(product, dict) else {}
    
    product_text = props.get("productText", "")
    issued_time = props.get("issuanceTime", "")
    product_id = props.get("id", "")
    
    # Generate a stable ID from office + issued time
    if product_id:
        doc_id = f"afd_{product_id}"
    else:
        id_string = f"{office}_{issued_time}"
        doc_id = f"afd_{hashlib.md5(id_string.encode()).hexdigest()[:16]}"
    
    return {
        "id": doc_id,
        "location": location,
        "source_type": "forecast",
        "headline": f"Area Forecast Discussion - {office}",
        "event": "AFD",
        "narrative_text": product_text,
        "issued_at": parse_timestamp(issued_time),
        "effective_at": parse_timestamp(issued_time),
        "payload": product,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


def normalize_forecast_period_to_document(period: dict, location: str, office: str) -> dict:
    """
    Normalize a forecast period into a document record.
    
    Args:
        period: A single period from the NWS forecast response
        location: Human-readable location string
        office: NWS office code
    
    Returns:
        Document dict with all required fields
    """
    # Generate stable ID from location + period number + start time
    period_number = period.get("number", 0)
    start_time = period.get("startTime", "")
    id_string = f"{location}_{period_number}_{start_time}"
    doc_id = f"forecast_{hashlib.md5(id_string.encode()).hexdigest()[:16]}"
    
    name = period.get("name", "")  # e.g. "Tonight", "Wednesday"
    short_forecast = period.get("shortForecast", "")
    detailed_forecast = period.get("detailedForecast", "")
    
    return {
        "id": doc_id,
        "location": location,
        "source_type": "forecast",
        "headline": f"{name}: {short_forecast}",
        "event": "Forecast",
        "narrative_text": detailed_forecast,
        "issued_at": parse_timestamp(start_time),
        "effective_at": parse_timestamp(start_time),
        "payload": period,
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }


def parse_location(location_str: str) -> dict:
    """
    Parse a location string into lat/lon coordinates.
    
    Supports:
    - "lat,lon" format: "41.88,-87.63"
    - City/state format: "Chicago, IL" (requires geocoding - simplified here)
    
    Returns: {"lat": float, "lon": float, "display": str}
    """
    location_str = location_str.strip()
    
    # Try to parse as "lat,lon"
    if re.match(r"^-?\d+\.?\d*\s*,\s*-?\d+\.?\d*$", location_str):
        parts = location_str.split(",")
        lat = float(parts[0].strip())
        lon = float(parts[1].strip())
        return {"lat": lat, "lon": lon, "display": location_str}
    
    # Expanded city coordinates (major US cities)
    city_coords = {
        # Major metros
        "chicago, il": (41.8781, -87.6298),
        "austin, tx": (30.2672, -97.7431),
        "new york, ny": (40.7128, -74.0060),
        "los angeles, ca": (34.0522, -118.2437),
        "miami, fl": (25.7617, -80.1918),
        "seattle, wa": (47.6062, -122.3321),
        "denver, co": (39.7392, -104.9903),
        "boston, ma": (42.3601, -71.0589),
        "san francisco, ca": (37.7749, -122.4194),
        "portland, or": (45.5152, -122.6784),
        
        # Additional major cities
        "houston, tx": (29.7604, -95.3698),
        "dallas, tx": (32.7767, -96.7970),
        "phoenix, az": (33.4484, -112.0740),
        "philadelphia, pa": (39.9526, -75.1652),
        "san antonio, tx": (29.4241, -98.4936),
        "san diego, ca": (32.7157, -117.1611),
        "atlanta, ga": (33.7490, -84.3880),
        "detroit, mi": (42.3314, -83.0458),
        "minneapolis, mn": (44.9778, -93.2650),
        "tampa, fl": (27.9506, -82.4572),
        "orlando, fl": (28.5383, -81.3792),
        "charlotte, nc": (35.2271, -80.8431),
        "nashville, tn": (36.1627, -86.7816),
        "las vegas, nv": (36.1699, -115.1398),
        "salt lake city, ut": (40.7608, -111.8910),
        "indianapolis, in": (39.7684, -86.1581),
        "columbus, oh": (39.9612, -82.9988),
        "kansas city, mo": (39.0997, -94.5786),
        "milwaukee, wi": (43.0389, -87.9065),
        "oklahoma city, ok": (35.4676, -97.5164),
        "albuquerque, nm": (35.0844, -106.6504),
        "baltimore, md": (39.2904, -76.6122),
        "pittsburgh, pa": (40.4406, -79.9959),
        "raleigh, nc": (35.7796, -78.6382),
        "richmond, va": (37.5407, -77.4360),
        "memphis, tn": (35.1495, -90.0490),
        "louisville, ky": (38.2527, -85.7585),
        "jacksonville, fl": (30.3322, -81.6557),
        "new orleans, la": (29.9511, -90.0715),
        "birmingham, al": (33.5186, -86.8104),
        "little rock, ar": (34.7465, -92.2896),
        "boise, id": (43.6150, -116.2023),
        "anchorage, ak": (61.2181, -149.9003),
        "honolulu, hi": (21.3099, -157.8581),
        
        # Texas cities (expanded)
        "plano, texas": (33.0198, -96.6989),
        "plano, tx": (33.0198, -96.6989),
        "fort worth, tx": (32.7555, -97.3308),
        "arlington, tx": (32.7357, -97.1081),
        "corpus christi, tx": (27.8006, -97.3964),
        "el paso, tx": (31.7619, -106.4850),
        "lubbock, tx": (33.5779, -101.8552),
    }
    
    # State centers (for state-code queries like "TX")
    state_coords = {
        "tx": (31.9686, -99.9018), "texas": (31.9686, -99.9018),
        "ca": (36.7783, -119.4179), "california": (36.7783, -119.4179),
        "fl": (27.9944, -81.7603), "florida": (27.9944, -81.7603),
        "ny": (43.2994, -74.2179), "new york": (43.2994, -74.2179),
        "il": (40.6331, -89.3985), "illinois": (40.6331, -89.3985),
        "pa": (41.2033, -77.1945), "pennsylvania": (41.2033, -77.1945),
        "oh": (40.4173, -82.9071), "ohio": (40.4173, -82.9071),
        "mi": (44.3148, -85.6024), "michigan": (44.3148, -85.6024),
        "ga": (32.1656, -82.9001), "georgia": (32.1656, -82.9001),
        "nc": (35.7596, -79.0193), "north carolina": (35.7596, -79.0193),
        "wa": (47.7511, -120.7401), "washington": (47.7511, -120.7401),
        "az": (34.0489, -111.0937), "arizona": (34.0489, -111.0937),
        "ma": (42.4072, -71.3824), "massachusetts": (42.4072, -71.3824),
        "co": (39.5501, -105.7821), "colorado": (39.5501, -105.7821),
        "or": (43.8041, -120.5542), "oregon": (43.8041, -120.5542),
    }
    
    # Normalize location string
    key = location_str.lower().strip()
    
    # Check city coordinates
    if key in city_coords:
        lat, lon = city_coords[key]
        return {"lat": lat, "lon": lon, "display": location_str}
    
    # Check state coordinates
    if key in state_coords:
        lat, lon = state_coords[key]
        return {"lat": lat, "lon": lon, "display": location_str}
    
    # Fall back to Nominatim geocoding for unknown locations
    try:
        geocode_url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": location_str,
            "format": "json",
            "limit": 1,
            "countrycodes": "us",  # Restrict to US
        }
        headers = {"User-Agent": "WeatherRAGApp/1.0 (Databricks)"}
        
        response = requests.get(geocode_url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        results = response.json()
        
        if results:
            lat = float(results[0]["lat"])
            lon = float(results[0]["lon"])
            return {"lat": lat, "lon": lon, "display": location_str}
    except Exception as e:
        # Geocoding failed, raise original error
        pass
    
    raise ValueError(
        f"Could not parse location: {location_str}. "
        f"Use 'lat,lon' format (e.g. '41.88,-87.63'), a known city/state, or a US state code."
    )

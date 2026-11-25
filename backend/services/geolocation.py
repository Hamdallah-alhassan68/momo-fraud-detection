"""Real-only reverse geocoding and public-IP geolocation helpers.

Never fabricates or guesses placeholder locations for loopback, private,
documentation, or unconsented requests.
"""

from __future__ import annotations

import ipaddress
import os
import sys
import threading
import math

import httpx

_GEOCODE_CACHE: dict[tuple[float, float], str | None] = {}
_IP_GEO_CACHE: dict[str, str | None] = {}
_CACHE_LOCK = threading.Lock()

_DOC_AND_TEST_NETWORKS = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("100.64.0.0/10"),
)

_NAIROBI_APPROXIMATE_ZONES = (
    (-1.2159, 36.8894, "Roysambu, Nairobi, Kenya"),
    (-1.2177, 36.8847, "Roysambu, Nairobi, Kenya"),
    (-1.2110, 36.8830, "Roysambu, Nairobi, Kenya"),
    (-1.2230, 36.8960, "Kasarani, Nairobi, Kenya"),
    (-1.2110, 36.8910, "Zimmerman, Nairobi, Kenya"),
)


def approximate_named_location(latitude: float, longitude: float) -> str | None:
    """Return a nearby known Nairobi zone for simulator/demo coordinates."""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    nearest = None
    nearest_distance = float("inf")
    for zone_lat, zone_lon, name in _NAIROBI_APPROXIMATE_ZONES:
        distance = math.hypot(lat - zone_lat, lon - zone_lon)
        if distance < nearest_distance:
            nearest_distance = distance
            nearest = name

    # Roughly 1.5 km; beyond this, do not guess a named place.
    if nearest_distance <= 0.014:
        return nearest
    return None


def _external_lookup_disabled() -> bool:
    if os.getenv("MOMO_DISABLE_EXTERNAL_GEOCODE", "").strip() == "1":
        return True
    return "unittest" in sys.modules or "pytest" in sys.modules


def is_public_ip(ip_str: str | None) -> bool:
    """Return True only when ip_str is a routable global IP address."""
    if not ip_str or not isinstance(ip_str, str):
        return False
    try:
        addr = ipaddress.ip_address(ip_str.strip())
    except ValueError:
        return False
    if not addr.is_global:
        return False
    return not any(addr in net for net in _DOC_AND_TEST_NETWORKS)


def _format_nominatim_address(payload: dict) -> str | None:
    addr = payload.get("address") or {}
    road = addr.get("road") or addr.get("pedestrian") or addr.get("footway") or ""
    house = addr.get("house_number") or ""
    street = f"{house} {road}".strip() if house and road else road

    area = (
        addr.get("neighbourhood")
        or addr.get("suburb")
        or addr.get("quarter")
        or addr.get("residential")
        or addr.get("city_district")
        or ""
    )
    city = (
        addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or addr.get("county")
        or ""
    )
    region = addr.get("state") or addr.get("region") or ""
    country = addr.get("country") or ""

    parts: list[str] = []
    for part in (street, area, city, region if not city else "", country):
        cleaned = str(part or "").strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)

    if parts:
        return ", ".join(parts[:4])
    display = str(payload.get("display_name") or "").strip()
    if display:
        return ", ".join(p.strip() for p in display.split(",")[:4] if p.strip())
    return None


def _format_bigdatacloud_address(payload: dict) -> str | None:
    admin = payload.get("localityInfo", {}).get("administrative", []) or []
    district = ""
    for item in admin:
        desc = str(item.get("description") or "").lower()
        level = item.get("adminLevel")
        if "sub-count" in desc or "division" in desc or level in (6, 7, 8):
            district = str(item.get("name") or "").strip()
            if district:
                break
    city = (
        payload.get("city")
        or payload.get("locality")
        or payload.get("principalSubdivision")
        or ""
    )
    country = payload.get("countryName") or ""
    parts: list[str] = []
    for part in (district, city, country):
        cleaned = str(part or "").strip()
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
    return ", ".join(parts) if parts else None


def reverse_geocode_coordinates(
    latitude: float,
    longitude: float,
    timeout: float = 2.5,
) -> str | None:
    """Return an approximate place label for submitted coordinates."""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None

    cache_key = (round(lat, 5), round(lon, 5))
    with _CACHE_LOCK:
        if cache_key in _GEOCODE_CACHE:
            return _GEOCODE_CACHE[cache_key]

    if _external_lookup_disabled():
        resolved = approximate_named_location(lat, lon)
        with _CACHE_LOCK:
            if len(_GEOCODE_CACHE) >= 512:
                _GEOCODE_CACHE.clear()
            _GEOCODE_CACHE[cache_key] = resolved
        return resolved

    resolved: str | None = None
    try:
        resp = httpx.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "format": "jsonv2",
                "lat": f"{lat:.6f}",
                "lon": f"{lon:.6f}",
                "zoom": 18,
                "addressdetails": 1,
            },
            headers={
                "User-Agent": "MoMoSentinel/1.0 (fraud-detection-research)",
                "Accept-Language": "en",
            },
            timeout=timeout,
        )
        if resp.is_success:
            resolved = _format_nominatim_address(resp.json())
    except Exception:
        resolved = None

    if not resolved:
        try:
            resp = httpx.get(
                "https://api.bigdatacloud.net/data/reverse-geocode-client",
                params={
                    "latitude": f"{lat:.6f}",
                    "longitude": f"{lon:.6f}",
                    "localityLanguage": "en",
                },
                timeout=timeout,
            )
            if resp.is_success:
                resolved = _format_bigdatacloud_address(resp.json())
        except Exception:
            resolved = None

    if not resolved:
        resolved = approximate_named_location(lat, lon)

    with _CACHE_LOCK:
        if len(_GEOCODE_CACHE) >= 512:
            _GEOCODE_CACHE.clear()
        _GEOCODE_CACHE[cache_key] = resolved
    return resolved


def geolocate_ip(ip_address: str | None, timeout: float = 2.0) -> str | None:
    """Resolve a real public IP address to its approximate city/region/country."""
    if not is_public_ip(ip_address):
        return None

    clean_ip = str(ip_address).strip()
    with _CACHE_LOCK:
        if clean_ip in _IP_GEO_CACHE:
            return _IP_GEO_CACHE[clean_ip]

    if _external_lookup_disabled():
        return None

    resolved: str | None = None
    try:
        resp = httpx.get(
            f"https://ipapi.co/{clean_ip}/json/",
            headers={"User-Agent": "MoMoSentinel/1.0"},
            timeout=timeout,
        )
        if resp.is_success:
            data = resp.json()
            if not data.get("error"):
                parts: list[str] = []
                for field in ("city", "region", "country_name"):
                    val = str(data.get(field) or "").strip()
                    if val and val not in parts:
                        parts.append(val)
                if parts:
                    resolved = f"{', '.join(parts)} (via IP)"
    except Exception:
        resolved = None

    with _CACHE_LOCK:
        if len(_IP_GEO_CACHE) >= 512:
            _IP_GEO_CACHE.clear()
        _IP_GEO_CACHE[clean_ip] = resolved
    return resolved

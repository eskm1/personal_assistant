import urllib.parse


def get_directions(origin: str, destination: str, mode: str = "driving") -> str:
    """Generate a Google Maps directions link between two places."""
    encoded_origin = urllib.parse.quote(origin)
    encoded_dest = urllib.parse.quote(destination)
    maps_url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={encoded_origin}"
        f"&destination={encoded_dest}"
        f"&travelmode={mode}"
    )
    mode_emoji = {"driving": "🚗", "walking": "🚶", "transit": "🚌", "bicycling": "🚲"}.get(mode, "🗺")
    return f"{mode_emoji} {origin} → {destination}\n📍 {maps_url}"


TOOL_DEFS = [
    {
        "name": "get_directions",
        "description": (
            "Generate a Google Maps link for directions between two places. "
            "Supports driving, walking, transit, and bicycling modes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "Starting address or place name"},
                "destination": {"type": "string", "description": "Destination address or place name"},
                "mode": {
                    "type": "string",
                    "description": "Travel mode: driving, walking, transit, or bicycling",
                    "enum": ["driving", "walking", "transit", "bicycling"],
                    "default": "driving",
                },
            },
            "required": ["origin", "destination"],
        },
    },
]

DISPATCH = {
    "get_directions": get_directions,
}

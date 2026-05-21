import json
from datetime import datetime, timezone
from urllib.request import urlopen

events = []

# -----------------------------
# Titled Tuesday starter event
# -----------------------------

events.append({
    "id": "titled-tuesday",
    "title": "Titled Tuesday",
    "shortTitle": "Titled Tuesday",
    "status": "upcoming",
    "startDate": "2026-01-01T17:00:00Z",
    "endDate": "2026-01-01T20:00:00Z",
    "timezone": "Europe/Zurich",
    "locationName": "Online",
    "isOnline": True,
    "summary": "Weekly Chess.com titled-player online event.",
    "description": "Titled Tuesday is a regular online event for titled chess players.",
    "categories": ["online", "blitz"],
    "playerIds": [],
    "channelIds": ["chesscom"],
    "primaryUrl": "https://www.chess.com/article/view/titled-tuesday",
    "links": [
        {
            "label": "Chess.com event info",
            "type": "official",
            "url": "https://www.chess.com/article/view/titled-tuesday"
        },
        {
            "label": "Chess.com TV",
            "type": "watch",
            "url": "https://www.chess.com/tv"
        }
    ]
})

# -----------------------------
# Lichess broadcasts
# -----------------------------

try:
    lichess_url = "https://lichess.org/api/broadcast"
    
    with urlopen(lichess_url) as response:
        data = json.loads(response.read().decode())

    for item in data[:10]:
        event = {
            "id": f"lichess-{item.get('id', '')}",
            "title": item.get("name", "Lichess Broadcast"),
            "shortTitle": item.get("name", "Broadcast"),
            "status": "live",
            "startDate": datetime.now(timezone.utc).isoformat(),
            "endDate": datetime.now(timezone.utc).isoformat(),
            "timezone": "UTC",
            "locationName": "Online",
            "isOnline": True,
            "summary": "Live chess broadcast on Lichess.",
            "description": item.get("description", ""),
            "categories": ["broadcast"],
            "playerIds": [],
            "channelIds": ["lichess"],
            "primaryUrl": f"https://lichess.org/broadcast/{item.get('slug', '')}/{item.get('id', '')}",
            "links": [
                {
                    "label": "Watch on Lichess",
                    "type": "watch",
                    "url": f"https://lichess.org/broadcast/{item.get('slug', '')}/{item.get('id', '')}"
                }
            ]
        }

        events.append(event)

except Exception as e:
    print("Could not fetch Lichess broadcasts:", e)

# -----------------------------
# Final output
# -----------------------------

output = {
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "eventCount": len(events),
    "events": events
}

with open("events.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"Generated events.json with {len(events)} events")

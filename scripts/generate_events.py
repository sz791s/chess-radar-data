import json
from datetime import datetime, timezone
from urllib.request import urlopen

events = []

def ms_to_iso(ms):
    if not ms:
        return datetime.now(timezone.utc).isoformat()
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()

# -----------------------------------
# Titled Tuesday starter event
# -----------------------------------

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

# -----------------------------------
# Lichess broadcasts
# -----------------------------------

try:
    lichess_url = "https://lichess.org/api/broadcast"

    with urlopen(lichess_url) as response:
        raw = response.read().decode()

    lines = raw.strip().splitlines()

    for line in lines[:10]:
        item = json.loads(line)

        tour = item.get("tour", {})
        info = tour.get("info", {})

        broadcast_id = tour.get("id", "")
        name = tour.get("name", "Lichess Broadcast")
        slug = tour.get("slug", "")
        url = tour.get("url", "")

        dates = tour.get("dates", [])
        start_ms = dates[0] if len(dates) > 0 else None
        end_ms = dates[1] if len(dates) > 1 else None

        location = info.get("location", "Online")
        timezone_name = info.get("timeZone", "UTC")
        fide_tc = info.get("fideTC", "")
        format_text = info.get("format", "")

        categories = ["broadcast"]

        if fide_tc:
            categories.append(fide_tc.lower())

        if "rapid" in format_text.lower():
            categories.append("rapid")
        elif "blitz" in format_text.lower():
            categories.append("blitz")
        elif "classical" in format_text.lower() or fide_tc == "standard":
            categories.append("classical")

        links = [
            {
                "label": "Watch on Lichess",
                "type": "liveBoards",
                "url": url or f"https://lichess.org/broadcast/{slug}/{broadcast_id}"
            }
        ]

        if info.get("website"):
            links.append({
                "label": "Official website",
                "type": "official",
                "url": info["website"]
            })

        if info.get("standings"):
            links.append({
                "label": "Standings",
                "type": "results",
                "url": info["standings"]
            })

        event = {
            "id": f"lichess-{broadcast_id}",
            "title": name,
            "shortTitle": name,
            "status": "upcoming",
            "startDate": ms_to_iso(start_ms),
            "endDate": ms_to_iso(end_ms),
            "timezone": timezone_name,
            "locationName": location,
            "isOnline": location.lower() == "online",
            "summary": "Chess tournament broadcast with live boards on Lichess.",
            "description": format_text or "Chess tournament broadcast with live boards on Lichess.",
            "categories": list(dict.fromkeys(categories)),
            "playerIds": [],
            "channelIds": ["lichess"],
            "primaryUrl": url or f"https://lichess.org/broadcast/{slug}/{broadcast_id}",
            "links": links
        }

        events.append(event)

except Exception as e:
    print("Could not fetch Lichess broadcasts:", e)

# -----------------------------------
# Final output
# -----------------------------------

output = {
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "eventCount": len(events),
    "events": events
}

with open("events.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"Generated events.json with {len(events)} events")

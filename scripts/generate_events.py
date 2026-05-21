import json
from datetime import datetime, timezone

events = [
    {
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
        "description": "Titled Tuesday is a regular online event for titled chess players. Check Chess.com for the latest confirmed schedule and broadcast details.",
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
        ],
        "calendarTitle": "Titled Tuesday",
        "calendarNotes": "Check Chess.com for the latest Titled Tuesday schedule and broadcast details."
    }
]

output = {
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "events": events
}

with open("events.json", "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print("Generated events.json")

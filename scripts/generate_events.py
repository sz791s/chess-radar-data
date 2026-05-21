import json
import logging
import re
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
LICHESS_BROADCAST_URL = "https://lichess.org/api/broadcast"
FIDE_CALENDAR_URL = "https://calendar.fide.com/calendar.php"
FIDE_CALENDAR_ENDPOINT = "https://calendar.fide.com/calendar_server.php"
USER_AGENT = "ChessRadarData/1.0 (+https://sz791s.github.io/chess-radar-data/events.json)"

EVENT_STATUSES = {"upcoming", "live", "completed", "tentative"}
SOURCE_PRIORITY = {"lichess": 0, "fide": 1, "channel": 2, "organiser": 3, "streamer": 4, "curated": 5}
MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
ALLOWED_CATEGORIES = {
    "classical",
    "rapid",
    "blitz",
    "online",
    "broadcast",
    "women",
    "team",
    "freestyle",
    "junior",
    "open",
    "elite",
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return to_iso(now_utc())


def to_iso(value):
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(value):
    value = unescape(value or "").lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "event"


def parse_date_safely(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def ms_to_iso(ms):
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return to_iso(datetime.fromtimestamp(ms / 1000, tz=timezone.utc))


def compute_status(start_date, end_date, tentative=False):
    if tentative:
        return "tentative"
    start = parse_date_safely(start_date)
    end = parse_date_safely(end_date)
    now = now_utc()
    if start and end and start <= now <= end:
        return "live"
    if end and end < now:
        return "completed"
    if start and start > now:
        return "upcoming"
    return "tentative"


def compact_list(values):
    seen = set()
    result = []
    for value in values or []:
        if not value:
            continue
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def clean_text(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_links(links):
    result = []
    seen = set()
    for link in links or []:
        url = (link or {}).get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        result.append({
            "label": link.get("label") or "Link",
            "type": link.get("type") or "info",
            "url": url,
        })
    return result


def normalize_event(event):
    title = clean_text(event.get("title")) or "Chess Event"
    start_date = event.get("startDate")
    end_date = event.get("endDate") or start_date
    source = event.get("source") or {"name": "curated", "url": event.get("primaryUrl") or "", "confidence": "low"}
    status = event.get("status") or compute_status(start_date, end_date, source.get("confidence") == "low")
    if status not in EVENT_STATUSES:
        status = compute_status(start_date, end_date, source.get("confidence") == "low")
    links = normalize_links(event.get("links"))
    primary_url = event.get("primaryUrl") or (links[0]["url"] if links else "")
    return {
        "id": slugify(event.get("id") or title),
        "title": title,
        "shortTitle": clean_text(event.get("shortTitle")) or title,
        "status": status,
        "startDate": start_date,
        "endDate": end_date,
        "timezone": event.get("timezone") or "UTC",
        "locationName": clean_text(event.get("locationName")) or ("Online" if event.get("isOnline") else "TBD"),
        "isOnline": bool(event.get("isOnline")),
        "summary": clean_text(event.get("summary")) or "Chess event.",
        "description": clean_text(event.get("description")) or clean_text(event.get("summary")) or "Chess event.",
        "categories": [item for item in compact_list(event.get("categories")) if item in ALLOWED_CATEGORIES],
        "playerIds": compact_list(event.get("playerIds")),
        "channelIds": compact_list(event.get("channelIds")),
        "primaryUrl": primary_url,
        "links": links,
        "source": {
            "name": source.get("name") or "curated",
            "url": source.get("url") or primary_url,
            "confidence": source.get("confidence") or "low",
        },
    }


def add_source_result(results, name, status, count=0, error=None):
    item = {"name": name, "status": status, "count": count}
    if error:
        item["error"] = str(error)
    results.append(item)


def write_json(filename, payload):
    path = ROOT / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logging.info("Generated %s", filename)


def request_text(url, data=None, headers=None, timeout=20):
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    encoded = urlencode(data, doseq=True).encode("utf-8") if data else None
    request = Request(url, data=encoded, headers=request_headers)
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def event_in_window(event, past_days=90, future_days=365):
    start = parse_date_safely(event.get("startDate"))
    end = parse_date_safely(event.get("endDate"))
    if not start and not end:
        return event.get("status") == "tentative"
    start = start or end
    end = end or start
    lower = now_utc() - timedelta(days=past_days)
    upper = now_utc() + timedelta(days=future_days)
    return end >= lower and start <= upper


def title_key(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def dates_overlap(a, b):
    a_start = parse_date_safely(a.get("startDate"))
    b_start = parse_date_safely(b.get("startDate"))
    a_end = parse_date_safely(a.get("endDate")) or a_start
    b_end = parse_date_safely(b.get("endDate")) or b_start
    if not all([a_start, a_end, b_start, b_end]):
        return False
    return a_start <= b_end and b_start <= a_end


def merge_event_links(winner, loser):
    winner["links"] = normalize_links(winner.get("links", []) + loser.get("links", []))
    winner["channelIds"] = compact_list(winner.get("channelIds", []) + loser.get("channelIds", []))
    winner["playerIds"] = compact_list(winner.get("playerIds", []) + loser.get("playerIds", []))
    winner["categories"] = compact_list(winner.get("categories", []) + loser.get("categories", []))
    if not winner.get("primaryUrl") and loser.get("primaryUrl"):
        winner["primaryUrl"] = loser["primaryUrl"]
    return winner


def has_live_board_url(event):
    return any("lichess.org/broadcast" in link.get("url", "") for link in event.get("links", []))


def preferred_event(a, b):
    a_source = a.get("source", {}).get("name", "")
    b_source = b.get("source", {}).get("name", "")
    a_priority = SOURCE_PRIORITY.get(a_source, 9)
    b_priority = SOURCE_PRIORITY.get(b_source, 9)
    if a_source == "lichess" and has_live_board_url(a):
        a_priority = -1
    if b_source == "lichess" and has_live_board_url(b):
        b_priority = -1
    if a_priority != b_priority:
        return (a, b) if a_priority < b_priority else (b, a)
    return (a, b) if len(a.get("links", [])) >= len(b.get("links", [])) else (b, a)


def dedupe_events(events):
    deduped = []
    for event in events:
        event = normalize_event(event)
        if not event_in_window(event):
            continue
        duplicate_index = None
        for index, existing in enumerate(deduped):
            same_title = title_key(existing["title"]) == title_key(event["title"])
            same_slug = existing["id"] == event["id"]
            if (same_title or same_slug) and dates_overlap(existing, event):
                duplicate_index = index
                break
        if duplicate_index is None:
            deduped.append(event)
            continue
        winner, loser = preferred_event(deduped[duplicate_index], event)
        deduped[duplicate_index] = merge_event_links(winner, loser)
    return deduped


def sort_event_key(event):
    status_rank = {"live": 0, "upcoming": 1, "tentative": 2, "completed": 3}
    start = event.get("startDate") or "9999-12-31T23:59:59Z"
    if event.get("status") == "completed":
        start = event.get("endDate") or event.get("startDate") or "9999-12-31T23:59:59Z"
    return (status_rank.get(event.get("status"), 9), start, event.get("title", ""))


def infer_categories_from_text(*parts):
    text = " ".join(str(part or "") for part in parts).lower()
    categories = []
    if "rapid" in text:
        categories.append("rapid")
    if "blitz" in text:
        categories.append("blitz")
    if "standard" in text or "classical" in text:
        categories.append("classical")
    if "online" in text:
        categories.append("online")
    if "broadcast" in text:
        categories.append("broadcast")
    if "women" in text or "women's" in text or "womens" in text or "ladies" in text:
        categories.append("women")
    if "team" in text or "league" in text or "bundesliga" in text or "4ncl" in text:
        categories.append("team")
    if "freestyle" in text or "chess960" in text:
        categories.append("freestyle")
    if "junior" in text or "youth" in text:
        categories.append("junior")
    if "open" in text:
        categories.append("open")
    if "candidates" in text or "world cup" in text or "grand chess tour" in text or "elite" in text:
        categories.append("elite")
    return compact_list(categories)


def curated_major_events():
    raw_events = [
        {
            "id": "norway-chess-2026",
            "title": "Norway Chess 2026",
            "shortTitle": "Norway Chess",
            "startDate": "2026-05-25T13:00:00Z",
            "endDate": "2026-06-05T21:00:00Z",
            "timezone": "Europe/Oslo",
            "locationName": "Oslo, Norway",
            "summary": "Elite over-the-board tournament hosted by Norway Chess.",
            "description": "Norway Chess 2026 includes elite open and women's events.",
            "categories": ["classical", "elite"],
            "channelIds": ["norway-chess"],
            "primaryUrl": "https://norwaychess.no/en/schedule-2026/",
            "links": [{"label": "Official schedule", "type": "official", "url": "https://norwaychess.no/en/schedule-2026/"}],
            "source": {"name": "organiser", "url": "https://norwaychess.no/en/schedule-2026/", "confidence": "high"},
        },
        {
            "id": "titled-tuesday",
            "title": "Titled Tuesday",
            "shortTitle": "Titled Tuesday",
            "status": "tentative",
            "startDate": "2026-01-01T17:00:00Z",
            "endDate": "2026-12-31T20:00:00Z",
            "timezone": "UTC",
            "locationName": "Online",
            "isOnline": True,
            "summary": "Chess.com's recurring titled-player online blitz event.",
            "description": "Titled Tuesday is a recurring Chess.com online event; exact weekly dates are not listed in this static feed.",
            "categories": ["online", "blitz"],
            "channelIds": ["chesscom"],
            "primaryUrl": "https://www.chess.com/article/view/titled-tuesday",
            "links": [
                {"label": "Chess.com event info", "type": "official", "url": "https://www.chess.com/article/view/titled-tuesday"},
                {"label": "Chess.com TV", "type": "watch", "url": "https://www.chess.com/tv"},
            ],
            "source": {"name": "chesscom", "url": "https://www.chess.com/article/view/titled-tuesday", "confidence": "medium"},
        },
        {
            "id": "fide-world-rapid-blitz-2026",
            "title": "FIDE World Rapid & Blitz 2026",
            "shortTitle": "World Rapid & Blitz",
            "status": "tentative",
            "startDate": "2026-12-01T00:00:00Z",
            "endDate": "2026-12-31T23:59:59Z",
            "timezone": "UTC",
            "locationName": "TBD",
            "summary": "Annual FIDE rapid and blitz world championship event.",
            "description": "Placeholder for the annual FIDE World Rapid & Blitz; exact 2026 dates should be confirmed before display as a firm event.",
            "categories": ["rapid", "blitz", "elite"],
            "channelIds": ["fide"],
            "primaryUrl": "https://www.fide.com/",
            "links": [{"label": "FIDE", "type": "official", "url": "https://www.fide.com/"}],
            "source": {"name": "curated", "url": "https://www.fide.com/", "confidence": "low"},
        },
    ]
    return [normalize_event(event) for event in raw_events]


def organiser_event_links():
    organisers = [
        ("chesscom-events", "Chess.com Events", "Chess.com tournament and broadcast hub.", "Online", True, ["online"], ["chesscom"], "https://www.chess.com/events"),
        ("fide-calendar", "FIDE Calendar", "Official FIDE calendar of rated and official tournaments.", "Worldwide", False, ["classical", "rapid", "blitz"], ["fide"], FIDE_CALENDAR_URL),
        ("lichess-broadcasts", "Lichess Broadcasts", "Live-board broadcasts for tournaments around the world.", "Online", True, ["broadcast", "online"], ["lichess"], "https://lichess.org/broadcast"),
        ("saint-louis-chess-club-events", "Saint Louis Chess Club Events", "Major US tournaments and Saint Louis Chess Club broadcasts.", "Saint Louis, USA", False, ["classical", "elite"], ["saint-louis-chess-club"], "https://saintlouischessclub.org/events"),
        ("freestyle-chess-events", "Freestyle Chess Events", "Freestyle Chess event and broadcast hub.", "Worldwide", False, ["freestyle", "elite"], ["freestyle-chess"], "https://www.freestyle-chess.com/"),
        ("norway-chess-events", "Norway Chess Events", "Norway Chess official event hub.", "Norway", False, ["classical", "elite"], ["norway-chess"], "https://norwaychess.no/en/"),
        ("grand-chess-tour-events", "Grand Chess Tour Events", "Grand Chess Tour official event hub.", "Worldwide", False, ["classical", "rapid", "blitz", "elite"], ["grand-chess-tour"], "https://grandchesstour.org/"),
        ("take-take-take-events", "Take Take Take Event Coverage", "Chess event coverage and commentary hub.", "Online", True, ["broadcast", "online"], ["take-take-take"], "https://www.taketaketake.com/"),
        ("chessbase-india-events", "ChessBase India Event Coverage", "ChessBase India tournament coverage and videos.", "India", False, ["broadcast"], ["chessbase-india"], "https://www.chessbase.in/"),
    ]
    events = []
    for event_id, title, summary, location, is_online, categories, channel_ids, url in organisers:
        events.append(normalize_event({
            "id": event_id,
            "title": title,
            "shortTitle": title,
            "status": "tentative",
            "startDate": "2026-01-01T00:00:00Z",
            "endDate": "2026-12-31T23:59:59Z",
            "timezone": "UTC",
            "locationName": location,
            "isOnline": is_online,
            "summary": summary,
            "description": "Curated organiser source to help Chess Radar surface official chess-following opportunities. Exact individual event dates should come from stronger source entries.",
            "categories": categories,
            "channelIds": channel_ids,
            "primaryUrl": url,
            "links": [{"label": "Official event source", "type": "official", "url": url}],
            "source": {"name": "organiser", "url": url, "confidence": "low"},
        }))
    return events


def channel_event_links():
    channels = [
        {
            "id": "chesscom-live-events",
            "title": "Chess.com Live Events",
            "summary": "Chess.com live events, tournament pages, and broadcast links.",
            "location": "Online",
            "isOnline": True,
            "categories": ["online", "broadcast"],
            "channelIds": ["chesscom"],
            "links": [
                {"label": "Chess.com Events", "type": "official", "url": "https://www.chess.com/events"},
                {"label": "Chess.com TV", "type": "watch", "url": "https://www.chess.com/tv"},
                {"label": "Chess.com Twitch", "type": "watch", "url": "https://www.twitch.tv/chess"},
                {"label": "Chess.com YouTube", "type": "watch", "url": "https://www.youtube.com/@chesscom"},
            ],
        },
        {
            "id": "lichess-broadcast-calendar",
            "title": "Lichess Broadcast Calendar",
            "summary": "Lichess broadcast index with live boards for tournaments around the world.",
            "location": "Online",
            "isOnline": True,
            "categories": ["online", "broadcast"],
            "channelIds": ["lichess"],
            "links": [
                {"label": "Lichess Broadcasts", "type": "official", "url": "https://lichess.org/broadcast"},
                {"label": "Lichess TV", "type": "watch", "url": "https://lichess.org/tv"},
                {"label": "Lichess YouTube", "type": "watch", "url": "https://www.youtube.com/@lichessdotorg"},
            ],
        },
        {
            "id": "fide-event-coverage",
            "title": "FIDE Event Coverage",
            "summary": "FIDE official event calendar, news, and video coverage.",
            "location": "Worldwide",
            "isOnline": False,
            "categories": ["classical", "rapid", "blitz", "elite"],
            "channelIds": ["fide"],
            "links": [
                {"label": "FIDE Calendar", "type": "official", "url": FIDE_CALENDAR_URL},
                {"label": "FIDE News", "type": "official", "url": "https://www.fide.com/news"},
                {"label": "FIDE YouTube", "type": "watch", "url": "https://www.youtube.com/@FIDE_chess"},
            ],
        },
        {
            "id": "saint-louis-chess-club-broadcasts",
            "title": "Saint Louis Chess Club Broadcasts",
            "summary": "Saint Louis Chess Club tournament broadcasts and event coverage.",
            "location": "Saint Louis, USA",
            "isOnline": False,
            "categories": ["classical", "rapid", "blitz", "broadcast", "elite"],
            "channelIds": ["saint-louis-chess-club"],
            "links": [
                {"label": "Saint Louis events", "type": "official", "url": "https://saintlouischessclub.org/events"},
                {"label": "Saint Louis Twitch", "type": "watch", "url": "https://www.twitch.tv/stlchessclub"},
                {"label": "Saint Louis YouTube", "type": "watch", "url": "https://www.youtube.com/@STLChessClub"},
            ],
        },
        {
            "id": "freestyle-chess-broadcasts",
            "title": "Freestyle Chess Broadcasts",
            "summary": "Freestyle Chess event pages and official video coverage.",
            "location": "Worldwide",
            "isOnline": False,
            "categories": ["freestyle", "broadcast", "elite"],
            "channelIds": ["freestyle-chess"],
            "links": [
                {"label": "Freestyle Chess", "type": "official", "url": "https://www.freestyle-chess.com/"},
                {"label": "Freestyle Chess YouTube", "type": "watch", "url": "https://www.youtube.com/@FreestyleChess"},
            ],
        },
        {
            "id": "chessbase-india-coverage",
            "title": "ChessBase India Coverage",
            "summary": "ChessBase India tournament coverage, live streams, news, and videos.",
            "location": "India",
            "isOnline": False,
            "categories": ["broadcast"],
            "channelIds": ["chessbase-india"],
            "links": [
                {"label": "ChessBase India", "type": "official", "url": "https://www.chessbase.in/"},
                {"label": "ChessBase India YouTube", "type": "watch", "url": "https://www.youtube.com/@ChessBaseIndiachannel"},
            ],
        },
    ]
    events = []
    for channel in channels:
        primary_url = channel["links"][0]["url"]
        events.append(normalize_event({
            "id": channel["id"],
            "title": channel["title"],
            "shortTitle": channel["title"],
            "status": "tentative",
            "startDate": "2026-01-01T00:00:00Z",
            "endDate": "2026-12-31T23:59:59Z",
            "timezone": "UTC",
            "locationName": channel["location"],
            "isOnline": channel["isOnline"],
            "summary": channel["summary"],
            "description": "Curated channel source. This entry points users toward official event and watch pages; it does not claim a channel is live.",
            "categories": channel["categories"],
            "channelIds": channel["channelIds"],
            "primaryUrl": primary_url,
            "links": channel["links"],
            "source": {"name": "channel", "url": primary_url, "confidence": "low"},
        }))
    return events


def streamer_watch_events():
    streamers = [
        {
            "id": "hikaru-streams",
            "title": "Hikaru Streams",
            "shortTitle": "Hikaru",
            "summary": "GM Hikaru Nakamura's chess stream and video channels.",
            "channelIds": ["hikaru"],
            "links": [
                {"label": "Twitch channel", "type": "watch", "url": "https://www.twitch.tv/gmhikaru"},
                {"label": "Twitch schedule", "type": "schedule", "url": "https://www.twitch.tv/gmhikaru/schedule"},
                {"label": "YouTube channel", "type": "watch", "url": "https://www.youtube.com/@GMHikaru"},
            ],
        },
        {
            "id": "gothamchess-videos",
            "title": "GothamChess Videos",
            "shortTitle": "GothamChess",
            "summary": "Levy Rozman's chess recaps, lessons, tournament coverage, and commentary.",
            "channelIds": ["gothamchess"],
            "links": [{"label": "YouTube channel", "type": "watch", "url": "https://www.youtube.com/@GothamChess"}],
        },
        {
            "id": "botezlive-streams",
            "title": "BotezLive Streams",
            "shortTitle": "BotezLive",
            "summary": "Alexandra and Andrea Botez chess streams and videos.",
            "channelIds": ["botezlive"],
            "links": [
                {"label": "Twitch channel", "type": "watch", "url": "https://www.twitch.tv/botezlive"},
                {"label": "Twitch schedule", "type": "schedule", "url": "https://www.twitch.tv/botezlive/schedule"},
                {"label": "YouTube channel", "type": "watch", "url": "https://www.youtube.com/@BotezLive"},
            ],
        },
        {
            "id": "anna-cramling-streams",
            "title": "Anna Cramling Streams",
            "shortTitle": "Anna Cramling",
            "summary": "Anna Cramling's chess streams, videos, and event coverage.",
            "channelIds": ["anna-cramling"],
            "links": [
                {"label": "Twitch channel", "type": "watch", "url": "https://www.twitch.tv/annacramling"},
                {"label": "Twitch schedule", "type": "schedule", "url": "https://www.twitch.tv/annacramling/schedule"},
                {"label": "YouTube channel", "type": "watch", "url": "https://www.youtube.com/@AnnaCramling"},
            ],
        },
        {
            "id": "eric-rosen-streams",
            "title": "Eric Rosen Streams",
            "shortTitle": "Eric Rosen",
            "summary": "IM Eric Rosen's educational chess streams and videos.",
            "channelIds": ["eric-rosen"],
            "links": [
                {"label": "Twitch channel", "type": "watch", "url": "https://www.twitch.tv/imrosen"},
                {"label": "Twitch schedule", "type": "schedule", "url": "https://www.twitch.tv/imrosen/schedule"},
                {"label": "YouTube channel", "type": "watch", "url": "https://www.youtube.com/@EricRosen"},
            ],
        },
    ]
    events = []
    for streamer in streamers:
        primary_url = streamer["links"][0]["url"]
        events.append(normalize_event({
            "id": streamer["id"],
            "title": streamer["title"],
            "shortTitle": streamer["shortTitle"],
            "status": "tentative",
            "startDate": "2026-01-01T00:00:00Z",
            "endDate": "2026-12-31T23:59:59Z",
            "timezone": "UTC",
            "locationName": "Online",
            "isOnline": True,
            "summary": streamer["summary"],
            "description": "Curated streamer watch entry. Twitch schedule links are included where known, but no live status is claimed without an authenticated source.",
            "categories": ["online", "broadcast"],
            "channelIds": streamer["channelIds"],
            "primaryUrl": primary_url,
            "links": streamer["links"],
            "source": {"name": "streamer", "url": primary_url, "confidence": "low"},
        }))
    return events


def lichess_event_from_item(item):
    tour = item.get("tour") or item
    info = tour.get("info") or {}
    broadcast_id = tour.get("id") or item.get("id")
    name = tour.get("name") or item.get("name") or "Lichess Broadcast"
    slug = tour.get("slug") or slugify(name)
    url = tour.get("url") or f"https://lichess.org/broadcast/{slug}/{broadcast_id}"
    dates = tour.get("dates") or []
    start_date = ms_to_iso(dates[0]) if len(dates) > 0 else None
    end_date = ms_to_iso(dates[1]) if len(dates) > 1 else None
    location = info.get("location") or "Online"
    links = [{"label": "Watch on Lichess", "type": "liveBoards", "url": url}]
    if info.get("website"):
        links.append({"label": "Official website", "type": "official", "url": info["website"]})
    if info.get("standings"):
        links.append({"label": "Standings", "type": "results", "url": info["standings"]})
    return normalize_event({
        "id": f"lichess-{broadcast_id or slug}",
        "title": name,
        "shortTitle": name,
        "startDate": start_date,
        "endDate": end_date,
        "timezone": info.get("timeZone") or "UTC",
        "locationName": location,
        "isOnline": location.lower() == "online",
        "summary": "Chess tournament broadcast with live boards on Lichess.",
        "description": info.get("format") or "Chess tournament broadcast with live boards on Lichess.",
        "categories": infer_categories_from_text(info.get("fideTC"), info.get("format"), name, "broadcast"),
        "channelIds": ["lichess"],
        "primaryUrl": url,
        "links": links,
        "source": {"name": "lichess", "url": LICHESS_BROADCAST_URL, "confidence": "high"},
    })


def fetch_lichess_broadcasts(limit=25):
    raw = request_text(LICHESS_BROADCAST_URL, headers={"Accept": "application/x-ndjson"})
    events = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            events.append(lichess_event_from_item(json.loads(line)))
        except (TypeError, ValueError, KeyError) as exc:
            logging.error("Could not parse Lichess broadcast row: %s", exc)
        if len(events) >= limit:
            break
    return events


def parse_fide_date_text(date_text, fallback_year):
    text = clean_text(date_text).replace("\xa0", " ")
    text = re.sub(r"(\d)([A-Za-z])", r"\1 \2", text)
    parts = re.findall(r"\d{4}|[A-Za-z]+|\d{1,2}", text)
    if not parts:
        return None, None, True

    year_tokens = [int(part) for part in parts if re.fullmatch(r"\d{4}", part)]
    year = year_tokens[0] if year_tokens else fallback_year
    numbers = [int(part) for part in parts if re.fullmatch(r"\d{1,2}", part)]
    months = [MONTHS[part.lower()[:4] if part.lower().startswith("sept") else part.lower()[:3]] for part in parts if (part.lower()[:3] in MONTHS or part.lower()[:4] in MONTHS)]
    if not numbers or not months:
        return None, None, True

    if len(numbers) >= 2:
        start_day, end_day = numbers[0], numbers[1]
    else:
        start_day = end_day = numbers[0]
    start_month = months[0]
    end_month = months[-1]
    end_year = year + 1 if end_month < start_month else year
    try:
        start = datetime(year, start_month, start_day, tzinfo=timezone.utc)
        end = datetime(end_year, end_month, end_day, 23, 59, 59, tzinfo=timezone.utc)
    except ValueError:
        return None, None, True
    return to_iso(start), to_iso(end), False


def parse_fide_rows(html, fallback_year):
    rows = re.findall(r'<div class="calendar-h-row[^"]*".*?(?=<div class="calendar-h-row|<div class="ranking-pagination"|$)', html, re.S)
    events = []
    current_year = fallback_year
    previous_month = None
    for row in rows:
        title_match = re.search(r'<h3 class="calendar-h-name">\s*<a href="([^"]+)">(.*?)</a>', row, re.S)
        if not title_match:
            continue
        date_match = re.search(r'<div class="calendar-h-date-container[^"]*">(.*?)</div>', row, re.S)
        time_match = re.search(r'<p class="calendar-h-start">(.*?)</p>', row, re.S)
        city_match = re.search(r'<p class="calendar-h-city">(.*?)</p>', row, re.S)
        date_text = clean_text(date_match.group(1)) if date_match else ""
        if re.fullmatch(r"\d{4}", date_text):
            current_year = int(date_text)
            previous_month = None
            continue
        start_date, end_date, tentative = parse_fide_date_text(date_text, current_year)
        start = parse_date_safely(start_date)
        if start and previous_month and start.month < previous_month and previous_month - start.month > 6:
            start_date, end_date, tentative = parse_fide_date_text(date_text, current_year + 1)
            current_year += 1
            start = parse_date_safely(start_date)
        if start:
            previous_month = start.month
        title = clean_text(title_match.group(2))
        detail_url = urljoin(FIDE_CALENDAR_URL, title_match.group(1))
        time_text = clean_text(time_match.group(1)) if time_match else ""
        location = clean_text(city_match.group(1)) if city_match else "TBD"
        events.append(normalize_event({
            "id": f"fide-{slugify(title)}-{slugify(date_text)}",
            "title": title,
            "shortTitle": title,
            "status": "tentative" if tentative else None,
            "startDate": start_date,
            "endDate": end_date,
            "timezone": "UTC",
            "locationName": location,
            "isOnline": "online" in location.lower() or "platform" in time_text.lower(),
            "summary": f"FIDE calendar event: {title}.",
            "description": time_text or f"Listed on the FIDE calendar: {title}.",
            "categories": infer_categories_from_text(title, time_text, location),
            "channelIds": ["fide"],
            "primaryUrl": detail_url,
            "links": [
                {"label": "FIDE calendar listing", "type": "official", "url": detail_url},
                {"label": "FIDE calendar", "type": "source", "url": FIDE_CALENDAR_URL},
            ],
            "source": {"name": "fide", "url": FIDE_CALENDAR_URL, "confidence": "medium" if not tentative else "low"},
        }))
    return events


def fetch_fide_calendar(max_pages=35):
    start = (now_utc() - timedelta(days=90)).date().isoformat()
    end = (now_utc() + timedelta(days=365)).date().isoformat()
    events = []
    for page in range(1, max_pages + 1):
        data = {
            "country": "all",
            "name_filter": "",
            "event_type": "all",
            "time_control": "all",
            "page": page,
            "from_date": start,
            "to_date": end,
            "show": "table",
        }
        html = request_text(
            FIDE_CALENDAR_ENDPOINT,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": FIDE_CALENDAR_URL,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        page_events = parse_fide_rows(html, now_utc().year)
        if not page_events:
            break
        events.extend(page_events)
        if "ranking-pagination-word" not in html or "next" not in html.lower():
            break
    return events


def future_streamer_enrichment(events):
    # Future extension point: Twitch schedule/live APIs require auth, and YouTube
    # live discovery needs an API key or brittle scraping. Keep this no-op until
    # Chess Radar has an approved keyless or authenticated ingestion path.
    return events


def collect_source(name, collector, source_results):
    try:
        events = collector()
        add_source_result(source_results, name, "ok", len(events))
        return events
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        logging.error("%s source failed: %s", name, exc)
        add_source_result(source_results, name, "error", 0, exc)
        return []


def build_events():
    source_results = []
    events = []
    events.extend(collect_source("curated", curated_major_events, source_results))
    events.extend(collect_source("organiser", organiser_event_links, source_results))
    events.extend(collect_source("channel", channel_event_links, source_results))
    events.extend(collect_source("streamer", streamer_watch_events, source_results))
    events.extend(collect_source("lichess", fetch_lichess_broadcasts, source_results))
    events.extend(collect_source("fide", fetch_fide_calendar, source_results))
    events = future_streamer_enrichment(events)
    events = dedupe_events(events)
    events.sort(key=sort_event_key)
    return {
        "generatedAt": iso_now(),
        "eventCount": len(events),
        "sources": source_results,
        "events": events,
    }


def build_channels():
    channels = [
        ("chesscom", "Chess.com", "officialChannel", "Official Chess.com coverage, events, and broadcasts.", {"website": "https://www.chess.com/", "events": "https://www.chess.com/events", "youtube": "https://www.youtube.com/@chesscom", "twitch": "https://www.twitch.tv/chess"}),
        ("lichess", "Lichess", "officialChannel", "Official Lichess broadcasts and community chess coverage.", {"website": "https://lichess.org/", "broadcasts": "https://lichess.org/broadcast", "youtube": "https://www.youtube.com/@lichessdotorg"}),
        ("fide", "FIDE", "officialChannel", "Official International Chess Federation news and event coverage.", {"website": "https://www.fide.com/", "calendar": FIDE_CALENDAR_URL, "youtube": "https://www.youtube.com/@FIDE_chess"}),
        ("saint-louis-chess-club", "Saint Louis Chess Club", "officialChannel", "Tournament broadcasts and educational chess content from Saint Louis.", {"website": "https://saintlouischessclub.org/", "youtube": "https://www.youtube.com/@STLChessClub", "twitch": "https://www.twitch.tv/stlchessclub"}),
        ("freestyle-chess", "Freestyle Chess", "officialChannel", "Official Freestyle Chess tournament coverage.", {"website": "https://www.freestyle-chess.com/", "youtube": "https://www.youtube.com/@FreestyleChess"}),
        ("take-take-take", "Take Take Take", "creator", "Chess media and commentary platform focused on elite events.", {"website": "https://www.taketaketake.com/", "youtube": "https://www.youtube.com/@TakeTakeTakeApp"}),
        ("norway-chess", "Norway Chess", "officialChannel", "Official Norway Chess tournament news, schedules, and broadcasts.", {"website": "https://norwaychess.no/en/", "youtube": "https://www.youtube.com/@NorwayChess"}),
        ("grand-chess-tour", "Grand Chess Tour", "officialChannel", "Official Grand Chess Tour event coverage.", {"website": "https://grandchesstour.org/"}),
        ("chessbase-india", "ChessBase India", "creator", "ChessBase India tournament coverage, news, and videos.", {"website": "https://www.chessbase.in/", "youtube": "https://www.youtube.com/@ChessBaseIndiachannel"}),
        ("hikaru", "Hikaru", "player", "GM Hikaru Nakamura's chess streams and videos.", {"youtube": "https://www.youtube.com/@GMHikaru", "twitch": "https://www.twitch.tv/gmhikaru"}),
        ("gothamchess", "GothamChess", "creator", "Levy Rozman's chess recaps, lessons, and commentary.", {"youtube": "https://www.youtube.com/@GothamChess"}),
        ("botezlive", "BotezLive", "creator", "Alexandra and Andrea Botez chess streams and videos.", {"youtube": "https://www.youtube.com/@BotezLive", "twitch": "https://www.twitch.tv/botezlive"}),
        ("anna-cramling", "Anna Cramling", "creator", "Anna Cramling's chess videos and streams.", {"youtube": "https://www.youtube.com/@AnnaCramling", "twitch": "https://www.twitch.tv/annacramling"}),
        ("eric-rosen", "Eric Rosen", "creator", "IM Eric Rosen's educational chess videos and streams.", {"youtube": "https://www.youtube.com/@EricRosen", "twitch": "https://www.twitch.tv/imrosen"}),
    ]
    return {
        "generatedAt": iso_now(),
        "channelCount": len(channels),
        "channels": [{"id": item[0], "name": item[1], "category": item[2], "description": item[3], "links": item[4]} for item in channels],
    }


OPEN_PLAYER_SEEDS = [
    ("Magnus Carlsen", "Norway", "NOR"), ("Hikaru Nakamura", "United States", "USA"), ("Fabiano Caruana", "United States", "USA"), ("Arjun Erigaisi", "India", "IND"), ("Gukesh Dommaraju", "India", "IND"), ("R Praggnanandhaa", "India", "IND"), ("Nodirbek Abdusattorov", "Uzbekistan", "UZB"), ("Alireza Firouzja", "France", "FRA"), ("Ian Nepomniachtchi", "FIDE", "FID"), ("Wesley So", "United States", "USA"), ("Wei Yi", "China", "CHN"), ("Anish Giri", "Netherlands", "NED"), ("Vincent Keymer", "Germany", "GER"), ("Jan-Krzysztof Duda", "Poland", "POL"), ("Leinier Dominguez", "United States", "USA"), ("Maxime Vachier-Lagrave", "France", "FRA"), ("Levon Aronian", "United States", "USA"), ("Shakhriyar Mamedyarov", "Azerbaijan", "AZE"), ("Vidit Gujrathi", "India", "IND"), ("Parham Maghsoodloo", "Iran", "IRI"), ("Yu Yangyi", "China", "CHN"), ("Richard Rapport", "Romania", "ROU"), ("Daniil Dubov", "FIDE", "FID"), ("Vladimir Fedoseev", "Slovenia", "SLO"), ("Sam Shankland", "United States", "USA"), ("Hans Niemann", "United States", "USA"), ("Ray Robson", "United States", "USA"), ("Amin Tabatabaei", "Iran", "IRI"), ("Nihal Sarin", "India", "IND"), ("Alexey Sarana", "Serbia", "SRB"), ("Bogdan-Daniel Deac", "Romania", "ROU"), ("David Navara", "Czech Republic", "CZE"), ("Salem Saleh", "United Arab Emirates", "UAE"), ("Jorden van Foreest", "Netherlands", "NED"), ("Andrey Esipenko", "FIDE", "FID"), ("Boris Gelfand", "Israel", "ISR"), ("Etienne Bacrot", "France", "FRA"), ("Francisco Vallejo Pons", "Spain", "ESP"), ("Pentala Harikrishna", "India", "IND"), ("Kirill Shevchenko", "Romania", "ROU"), ("Martyn Kravtsiv", "Ukraine", "UKR"), ("Vladislav Artemiev", "FIDE", "FID"), ("Ivan Saric", "Croatia", "CRO"), ("David Anton Guijarro", "Spain", "ESP"), ("Alexandr Predke", "Serbia", "SRB"), ("Radoslaw Wojtaszek", "Poland", "POL"), ("Awonder Liang", "United States", "USA"), ("Matthias Bluebaum", "Germany", "GER"), ("Jeffery Xiong", "United States", "USA"), ("Wang Hao", "China", "CHN"),
]


WOMEN_PLAYER_SEEDS = [
    ("Ju Wenjun", "China", "CHN"), ("Hou Yifan", "China", "CHN"), ("Aleksandra Goryachkina", "FIDE", "FID"), ("Humpy Koneru", "India", "IND"), ("Lei Tingjie", "China", "CHN"), ("Tan Zhongyi", "China", "CHN"), ("Kateryna Lagno", "FIDE", "FID"), ("Mariya Muzychuk", "Ukraine", "UKR"), ("Anna Muzychuk", "Ukraine", "UKR"), ("Nana Dzagnidze", "Georgia", "GEO"), ("Harika Dronavalli", "India", "IND"), ("Bibisara Assaubayeva", "Kazakhstan", "KAZ"), ("Alexandra Kosteniuk", "Switzerland", "SUI"), ("Elisabeth Paehtz", "Germany", "GER"), ("Zhu Jiner", "China", "CHN"), ("Vaishali Rameshbabu", "India", "IND"), ("Polina Shuvalova", "FIDE", "FID"), ("Sarasadat Khademalsharieh", "Spain", "ESP"), ("Antoaneta Stefanova", "Bulgaria", "BUL"), ("Gunay Mammadzada", "Azerbaijan", "AZE"), ("Lela Javakhishvili", "Georgia", "GEO"), ("Nino Batsiashvili", "Georgia", "GEO"), ("Dinara Saduakassova", "Kazakhstan", "KAZ"), ("Irina Krush", "United States", "USA"), ("Stavroula Tsolakidou", "Greece", "GRE"), ("Oliwia Kiolbasa", "Poland", "POL"), ("Nurgyul Salimova", "Bulgaria", "BUL"), ("Divya Deshmukh", "India", "IND"), ("Alice Lee", "United States", "USA"), ("Anna Ushenina", "Ukraine", "UKR"), ("Bela Khotenashvili", "Georgia", "GEO"), ("Alina Kashlinskaya", "Poland", "POL"), ("Pia Cramling", "Sweden", "SWE"), ("Zhai Mo", "China", "CHN"), ("Gulrukhbegim Tokhirjonova", "United States", "USA"), ("Batkhuyag Munguntuul", "Mongolia", "MGL"), ("Sabrina Vega Gutierrez", "Spain", "ESP"), ("Jolanta Zawadzka", "Poland", "POL"), ("Hoang Thanh Trang", "Hungary", "HUN"), ("Valentina Gunina", "FIDE", "FID"), ("Anna Zatonskih", "United States", "USA"), ("Marsel Efroimski", "Israel", "ISR"), ("Monika Socko", "Poland", "POL"), ("Medina Warda Aulia", "Indonesia", "INA"), ("Qianyun Gong", "Singapore", "SGP"), ("Mai Narva", "Estonia", "EST"), ("Eline Roebers", "Netherlands", "NED"), ("Carissa Yip", "United States", "USA"), ("Teodora Injac", "Serbia", "SRB"), ("Lu Miaoyi", "China", "CHN"),
]


def player_entry(rank, name, country, country_code):
    return {
        "id": slugify(name),
        "name": name,
        "country": country,
        "countryCode": country_code,
        "title": "GM",
        "fideId": None,
        "rank": rank,
        "classicalRating": None,
        "rapidRating": None,
        "blitzRating": None,
        "profileLinks": {},
    }


def build_player_feed(list_name, seeds):
    players = [player_entry(rank, *seed) for rank, seed in enumerate(seeds[:50], start=1)]
    return {
        "generatedAt": iso_now(),
        "list": list_name,
        "source": "manualSeed",
        "playerCount": len(players),
        "players": players,
    }


def main():
    write_json("events.json", build_events())
    write_json("channels.json", build_channels())
    write_json("players_open.json", build_player_feed("top50_open", OPEN_PLAYER_SEEDS))
    write_json("players_women.json", build_player_feed("top50_women", WOMEN_PLAYER_SEEDS))


if __name__ == "__main__":
    main()

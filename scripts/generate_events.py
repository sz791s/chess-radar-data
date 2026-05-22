import json
import logging
import re
from csv import DictReader
from datetime import datetime, timedelta, timezone
from html import unescape
from io import StringIO
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
LICHESS_BROADCAST_URL = "https://lichess.org/api/broadcast"
FIDE_CALENDAR_URL = "https://calendar.fide.com/calendar.php"
FIDE_CALENDAR_ENDPOINT = "https://calendar.fide.com/calendar_server.php"
CHESSBASE_CALENDAR_URL = "https://en.chessbase.com/post/chess-calendar-2026"
CHESSAROUND_CALENDAR_URL = "https://calendar.chessaround.com/"
CHESSDOM_CALENDAR_URL = "https://calendar.chessdom.com/2026-calendar/"
CHESSDOM_CSV_URL = "https://calendar.chessdom.com/copy-of-chess-calendar-sheet1-3/"
CHESSMIX_CALENDAR_URL = "https://www.chessmix.com/chess-tournaments/"
CHESS_CALENDAR_NET_URL = "https://www.chesscalendar.net/"
CHESSCOM_TOURNAMENTS_URL = "https://www.chess.com/tournaments"
CHESSCOM_CURRENT_EVENTS_URL = "https://www.chess.com/events/current"
CHESSCOM_EVENTS_API_URL = "https://www.chess.com/events/v1/api/searchv2"
US_CHESS_PLAN_AHEAD_URL = "https://new.uschess.org/plan-ahead-calendar"
USER_AGENT = "ChessRadarData/1.0 (+https://sz791s.github.io/chess-radar-data/events.json)"

EVENT_STATUSES = {"upcoming", "live", "completed", "tentative"}
SOURCE_PRIORITY = {
    "lichess": 0,
    "fide": 1,
    "chessbase": 2,
    "chessdom": 3,
    "chesscom": 4,
    "organiser": 5,
    "chessaround": 6,
    "curated": 7,
}
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
    "otb",
}

KNOWN_PLAYER_IDS = {
    "anna cramling": "anna-cramling",
    "eric rosen": "eric-rosen",
    "fabiano caruana": "fabiano-caruana",
    "gukesh": "gukesh-dommaraju",
    "gukesh dommaraju": "gukesh-dommaraju",
    "hikaru": "hikaru-nakamura",
    "hikaru nakamura": "hikaru-nakamura",
    "levy rozman": "gothamchess",
    "magnus": "magnus-carlsen",
    "magnus carlsen": "magnus-carlsen",
}

PLAYER_COUNTRY_CODES = {
    "alireza-firouzja": "FRA",
    "anish-giri": "NED",
    "arjun-erigaisi": "IND",
    "bogdan-daniel-deac": "ROU",
    "divya-deshmukh": "IND",
    "fabiano-caruana": "USA",
    "gukesh-dommaraju": "IND",
    "humpy-koneru": "IND",
    "jorden-van-foreest": "NED",
    "ju-wenjun": "CHN",
    "magnus-carlsen": "NOR",
    "maxime-vachier-lagrave": "FRA",
    "praggnanandhaa-rameshbabu": "IND",
    "r-praggnanandhaa": "IND",
    "vincent-keymer": "GER",
    "wesley-so": "USA",
    "zhu-jiner": "CHN",
}

PLAYER_PROFILE_LINKS = {
    "anna-cramling": {
        "youtube": "https://www.youtube.com/@AnnaCramling",
        "twitch": "https://www.twitch.tv/annacramling",
    },
    "david-howell": {
        "officialWebsite": "https://www.howellchess.com/home",
        "lichess": "https://lichess.org/streamer/HowellHub",
        "twitch": "https://www.twitch.tv/howellhub",
    },
    "eric-rosen": {
        "youtube": "https://www.youtube.com/@EricRosen",
        "twitch": "https://www.twitch.tv/imrosen",
    },
    "hikaru-nakamura": {
        "officialWebsite": "https://www.hikarunakamura.com/",
        "lichess": "https://lichess.org/streamer/TSMFTXH",
        "youtube": "https://www.youtube.com/@GMHikaru",
        "twitch": "https://www.twitch.tv/gmhikaru",
    },
    "magnus-carlsen": {
        "officialWebsite": "https://www.magnuscarlsen.com/",
        "lichess": "https://lichess.org/@/DrNykterstein",
    },
    "simon-williams": {
        "officialWebsite": "https://gingergm.com/",
        "lichess": "https://lichess.org/streamer/gingergm",
        "youtube": "https://www.youtube.com/c/GingerGM",
        "twitch": "https://www.twitch.tv/gingergm",
    },
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


class SourceSkipped(Exception):
    pass


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
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d.%m.%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"):
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
    value = value.replace("\u200d", " ").replace("\ufeff", " ")
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
    is_online = bool(event.get("isOnline"))
    categories = [item for item in compact_list(event.get("categories")) if item in ALLOWED_CATEGORIES]
    categories.append("online" if is_online else "otb")
    categories = compact_list(categories)
    player_ids = compact_list(event.get("playerIds") or infer_player_ids_from_text(title, event.get("summary"), event.get("description")))
    return {
        "id": slugify(event.get("id") or title),
        "title": title,
        "shortTitle": clean_text(event.get("shortTitle")) or title,
        "status": status,
        "startDate": start_date,
        "endDate": end_date,
        "timezone": event.get("timezone") or "UTC",
        "locationName": clean_text(event.get("locationName")) or ("Online" if event.get("isOnline") else "TBD"),
        "isOnline": is_online,
        "summary": clean_text(event.get("summary")) or "Chess event.",
        "description": clean_text(event.get("description")) or clean_text(event.get("summary")) or "Chess event.",
        "categories": categories,
        "playerIds": player_ids,
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


def request_json(url, payload=None, headers=None, timeout=20):
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    body = None
    method = "GET"
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
        method = "POST"
    if headers:
        request_headers.update(headers)
    request = Request(url, data=body, headers=request_headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def event_duration_days(event):
    start = parse_date_safely(event.get("startDate"))
    end = parse_date_safely(event.get("endDate")) or start
    if not start or not end:
        return None
    return (end - start).total_seconds() / 86400


def event_in_window(event, past_days=1, future_days=365, max_duration_days=31):
    if event.get("status") == "completed":
        return False
    start = parse_date_safely(event.get("startDate"))
    end = parse_date_safely(event.get("endDate"))
    if not start and not end:
        return False
    start = start or end
    end = end or start
    duration_days = event_duration_days(event)
    if duration_days is not None and duration_days > max_duration_days:
        return False
    lower = now_utc() - timedelta(days=past_days)
    upper = now_utc() + timedelta(days=future_days)
    return end >= lower and start <= upper


def title_key(title):
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


TITLE_STOPWORDS = {
    "the",
    "chess",
    "tournament",
    "championship",
    "championships",
    "festival",
    "international",
    "open",
    "classic",
    "classical",
    "2025",
    "2026",
}

DISTINCT_TITLE_TOKENS = {
    "women",
    "womens",
    "girls",
    "rapid",
    "blitz",
    "junior",
    "youth",
    "cadet",
    "team",
    "freestyle",
    "fischer",
    "random",
    "960",
    "amateur",
    "masters",
    "master",
    "gm",
    "im",
    "fm",
    "section",
    "group",
    "category",
    "sub",
    "u8",
    "u10",
    "u12",
    "u14",
    "u16",
    "u18",
    "u20",
}


def title_tokens(title):
    normalized = (title or "").lower()
    roman_numbers = {
        " i ": " 1 ",
        " ii ": " 2 ",
        " iii ": " 3 ",
        " iv ": " 4 ",
        " v ": " 5 ",
        " vi ": " 6 ",
        " vii ": " 7 ",
        " viii ": " 8 ",
        " ix ": " 9 ",
        " x ": " 10 ",
    }
    normalized = f" {normalized} "
    for roman, number in roman_numbers.items():
        normalized = normalized.replace(roman, number)
    raw_tokens = re.findall(r"[a-z0-9]+", normalized)
    return [token for token in raw_tokens if token not in TITLE_STOPWORDS and not re.fullmatch(r"\d+(st|nd|rd|th)?", token)]


def title_similarity(a_title, b_title):
    a_tokens = set(title_tokens(a_title))
    b_tokens = set(title_tokens(b_title))
    if not a_tokens or not b_tokens:
        return 0
    overlap = len(a_tokens & b_tokens)
    return overlap / max(len(a_tokens), len(b_tokens))


def has_conflicting_distinct_tokens(a_title, b_title):
    a_tokens = set(title_tokens(a_title))
    b_tokens = set(title_tokens(b_title))
    a_distinct = a_tokens & DISTINCT_TITLE_TOKENS
    b_distinct = b_tokens & DISTINCT_TITLE_TOKENS
    return bool(a_distinct ^ b_distinct)


def similar_event_title(a, b):
    if title_key(a["title"]) == title_key(b["title"]) or a["id"] == b["id"]:
        return True
    if has_conflicting_distinct_tokens(a["title"], b["title"]):
        return False
    similarity = title_similarity(a["title"], b["title"])
    if similarity >= 0.72:
        return True
    a_key = " ".join(title_tokens(a["title"]))
    b_key = " ".join(title_tokens(b["title"]))
    return len(a_key) >= 10 and len(b_key) >= 10 and (a_key in b_key or b_key in a_key)


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
    a_info_score = len(a.get("links", [])) + len(a.get("playerIds", [])) + len(a.get("channelIds", [])) + len(a.get("categories", []))
    b_info_score = len(b.get("links", [])) + len(b.get("playerIds", [])) + len(b.get("channelIds", [])) + len(b.get("categories", []))
    return (a, b) if a_info_score >= b_info_score else (b, a)


def dedupe_events(events):
    deduped = []
    for event in events:
        event = normalize_event(event)
        if not event_in_window(event):
            continue
        duplicate_index = None
        for index, existing in enumerate(deduped):
            if similar_event_title(existing, event) and dates_overlap(existing, event):
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


def infer_channel_ids_from_text(*parts):
    text = " ".join(str(part or "") for part in parts).lower()
    channel_ids = []
    checks = [
        ("chess.com", "chesscom"),
        ("chesscom", "chesscom"),
        ("lichess", "lichess"),
        ("fide", "fide"),
        ("saint louis", "saint-louis-chess-club"),
        ("sinquefield", "saint-louis-chess-club"),
        ("freestyle", "freestyle-chess"),
        ("fischer random", "freestyle-chess"),
        ("norway chess", "norway-chess"),
        ("grand chess tour", "grand-chess-tour"),
        ("superbet", "grand-chess-tour"),
        ("superunited", "grand-chess-tour"),
        ("tata steel", "chessbase-india"),
    ]
    for needle, channel_id in checks:
        if needle in text:
            channel_ids.append(channel_id)
    return compact_list(channel_ids)


def infer_player_ids_from_text(*parts):
    text = " ".join(str(part or "") for part in parts).lower()
    player_ids = []
    for name, player_id in KNOWN_PLAYER_IDS.items():
        if re.search(rf"\b{re.escape(name)}\b", text):
            player_ids.append(player_id)
    return compact_list(player_ids)


def display_player_name(raw_name):
    name = clean_text(raw_name)
    if "," in name:
        last, first = [part.strip() for part in name.split(",", 1)]
        if first:
            name = f"{first} {last}"
    return re.sub(r"\s+", " ", name).strip()


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
    ]
    return [normalize_event(event) for event in raw_events]


def parse_calendar_date_range(date_text, fallback_year):
    text = clean_text(date_text)
    if not text:
        return None, None, True
    start_date, end_date, tentative = parse_fide_date_text(text, fallback_year)
    return start_date, end_date, tentative


def event_from_calendar_row(source_name, title, date_text, location, url, extra_text="", source_url="", confidence="medium"):
    title = clean_text(title)
    location = clean_text(location) or "TBD"
    start_date, end_date, tentative = parse_calendar_date_range(date_text, now_utc().year)
    if not title or not start_date:
        return None
    source_url = source_url or url
    categories = infer_categories_from_text(title, extra_text, location)
    channel_ids = infer_channel_ids_from_text(title, extra_text, location, url)
    is_online = "online" in location.lower() or "chess.com" in (url or "").lower()
    return normalize_event({
        "id": f"{source_name}-{slugify(title)}-{slugify(date_text)}",
        "title": title,
        "shortTitle": title,
        "status": "tentative" if tentative else None,
        "startDate": start_date,
        "endDate": end_date,
        "timezone": "UTC",
        "locationName": "Online" if is_online else location,
        "isOnline": is_online,
        "summary": f"Upcoming chess tournament: {title}.",
        "description": extra_text or f"Listed by {source_name}: {title}.",
        "categories": categories,
        "channelIds": channel_ids,
        "primaryUrl": url,
        "links": [
            {"label": "Event listing", "type": "official", "url": url},
            {"label": f"{source_name} calendar", "type": "source", "url": source_url},
        ],
        "source": {"name": source_name, "url": source_url, "confidence": confidence if not tentative else "low"},
    })


def fetch_chessbase_calendar():
    html = request_text(CHESSBASE_CALENDAR_URL)
    story_match = re.search(r'<div class="full-story".*?</BODY>', html, re.S | re.I)
    story = story_match.group(0) if story_match else html
    blocks = re.findall(r"<P>(.*?)</P>", story, re.S | re.I)
    events = []
    current_month = None
    year = 2026
    for block in blocks:
        if "<div" in block.lower() or "shop.chessbase.com" in block:
            continue
        month_match = re.search(r"<STRONG>(.*?)</STRONG>", block, re.S | re.I)
        if month_match:
            current_month = clean_text(month_match.group(1))
            continue
        parts = [clean_text(part) for part in re.split(r"<BR\s*/?>", block, flags=re.I)]
        parts = [part for part in parts if part and part.lower() != "and"]
        if len(parts) < 3:
            continue
        date_text, title, location = parts[0], parts[1], parts[2]
        if not re.search(r"\d", date_text):
            continue
        if current_month and not any(month in date_text.lower() for month in MONTHS):
            date_text = f"{date_text} {current_month}"
        if not re.search(r"\d{4}", date_text):
            date_text = f"{date_text} {year}"
        event = event_from_calendar_row(
            "chessbase",
            title,
            date_text,
            location,
            CHESSBASE_CALENDAR_URL,
            "Major event listed in the ChessBase 2026 tournament calendar.",
            CHESSBASE_CALENDAR_URL,
            "medium",
        )
        if event:
            events.append(event)
    return events


def fetch_chessaround_calendar(limit=250):
    html = request_text(CHESSAROUND_CALENDAR_URL)
    rows = re.findall(r"<li[^>]*>(.*?)</li>", html, re.S | re.I)
    events = []
    for row in rows:
        hidden = clean_text(re.search(r'<span class="hidden">(.*?)</span>', row, re.S | re.I).group(1)) if re.search(r'<span class="hidden">(.*?)</span>', row, re.S | re.I) else ""
        type_match = re.search(r'title="([^"]+)"\s+class="fa[^"]*"', row)
        event_type = clean_text(type_match.group(1)) if type_match else hidden
        date_match = re.search(r'<span class="date">\s*<span class="type">.*?</span>(.*?)</span>', row, re.S | re.I)
        country_match = re.search(r'<img class="country"[^>]+alt="([^"]+)"', row, re.S | re.I)
        location_match = re.search(r'<span class="location">.*?<a href="/tournament/view/\d+"[^>]*>(.*?)</a>', row, re.S | re.I)
        name_match = re.search(r'<span class="name">(.*?)</span>', row, re.S | re.I)
        detail_match = re.search(r'href="(/tournament/view/\d+)"', row)
        players_match = re.search(r'<a class="players" href="([^"]+)"', row)
        if not (date_match and name_match):
            continue
        country = clean_text(country_match.group(1)) if country_match else ""
        city = clean_text(location_match.group(1)) if location_match else ""
        location = ", ".join(part for part in [city, country] if part) or country or "TBD"
        detail_url = urljoin(CHESSAROUND_CALENDAR_URL, detail_match.group(1)) if detail_match else CHESSAROUND_CALENDAR_URL
        event = event_from_calendar_row(
            "chessaround",
            clean_text(name_match.group(1)),
            clean_text(date_match.group(1)),
            location,
            detail_url,
            f"Chessaround listing. Time control/type: {event_type}.",
            CHESSAROUND_CALENDAR_URL,
            "medium",
        )
        if event and event_type:
            event["categories"] = compact_list(infer_categories_from_text(event_type, event["title"]) + event["categories"])
        if event and players_match:
            event["links"] = normalize_links(event["links"] + [{"label": "Starting list", "type": "players", "url": players_match.group(1)}])
        if event:
            events.append(event)
        if len(events) >= limit:
            break
    return events


def parse_chessdom_date(value, fallback_year=2026):
    text = clean_text(value)
    if not text or not re.search(r"\d", text):
        return None
    if not re.search(r"\d{4}", text):
        text = f"{text} {fallback_year}"
    return parse_date_safely(text)


def fetch_chessdom_calendar():
    csv_text = request_text(CHESSDOM_CSV_URL)
    events = []
    for row in DictReader(StringIO(csv_text)):
        title = clean_text(row.get("Name"))
        if not title:
            continue
        start = parse_chessdom_date(row.get("Start date"))
        end = parse_chessdom_date(row.get("End date")) or start
        if not start:
            continue
        location = clean_text(row.get("Location")) or "TBD"
        url = clean_text(row.get("URL")) or CHESSDOM_CALENDAR_URL
        extra_text = " ".join(clean_text(row.get(key)) for key in ("Type", "Format", "Category", "Players") if row.get(key))
        event = normalize_event({
            "id": f"chessdom-{slugify(title)}-{start.year}",
            "title": title,
            "shortTitle": title,
            "startDate": to_iso(start),
            "endDate": to_iso(end.replace(hour=23, minute=59, second=59)),
            "timezone": "UTC",
            "locationName": location,
            "isOnline": "online" in location.lower(),
            "summary": f"Upcoming chess tournament: {title}.",
            "description": extra_text or "Listed in the Chessdom 2026 tournament calendar.",
            "categories": infer_categories_from_text(title, extra_text, location),
            "playerIds": infer_player_ids_from_text(title, row.get("Players")),
            "channelIds": infer_channel_ids_from_text(title, extra_text, location, url),
            "primaryUrl": url,
            "links": [
                {"label": "Event website", "type": "official", "url": url},
                {"label": "Chessdom calendar", "type": "source", "url": CHESSDOM_CALENDAR_URL},
            ],
            "source": {"name": "chessdom", "url": CHESSDOM_CALENDAR_URL, "confidence": "medium"},
        })
        events.append(event)
    return events


def fetch_chessmix_calendar():
    html = request_text(CHESSMIX_CALENDAR_URL)
    if "Subscribe now" in html or "If you are not subscribed" in html:
        raise SourceSkipped("public Chessmix page does not expose tournament rows without a subscription")
    return []


def fetch_chess_calendar_net():
    html = request_text(CHESS_CALENDAR_NET_URL)
    if "apps.apple.com" in html and "play.google.com" in html:
        raise SourceSkipped("public chesscalendar.net page is an app landing page, not a dated event feed")
    return []


def fetch_chesscom_tournaments():
    html = request_text(CHESSCOM_TOURNAMENTS_URL)
    if "Daily Tournaments" in html:
        raise SourceSkipped("public Chess.com tournaments page lists ongoing daily tournaments without calendar start dates")
    return []


def stream_duration_hours(stream):
    start = parse_date_safely(stream.get("startAt"))
    end = parse_date_safely(stream.get("endAt"))
    if not start or not end:
        return None
    return (end - start).total_seconds() / 3600


def chesscom_stream_link(stream):
    stream_type = clean_text(stream.get("type")).lower()
    channel = clean_text(stream.get("channel"))
    if not stream_type or not channel:
        return None
    if stream_type == "twitch":
        url = channel if channel.startswith("http") else f"https://www.twitch.tv/{channel}"
        label = clean_text(stream.get("shortTitle")) or clean_text(stream.get("title")) or f"Twitch: {channel}"
        return {"label": label, "type": "stream", "url": url}
    if stream_type == "youtube":
        url = channel if channel.startswith("http") else f"https://www.youtube.com/{channel}"
        label = clean_text(stream.get("shortTitle")) or clean_text(stream.get("title")) or "YouTube stream"
        return {"label": label, "type": "stream", "url": url}
    return None


def chesscom_channel_ids(event, streams):
    ids = ["chesscom"]
    stream_channels = {
        "chess": "chesscom",
        "chesscom": "chesscom",
        "gmhikaru": "hikaru",
        "gothamchess": "gothamchess",
        "botezlive": "botezlive",
        "annacramling": "anna-cramling",
        "imrosen": "eric-rosen",
        "howellhub": "david-howell",
        "gingergm": "gingergm",
    }
    for stream in streams:
        channel = clean_text(stream.get("channel")).lower().rstrip("/")
        channel = channel.split("/")[-1] if "/" in channel else channel
        if channel in stream_channels:
            ids.append(stream_channels[channel])
    ids.extend(infer_channel_ids_from_text(event.get("name"), event.get("featuredTitle"), event.get("eventType")))
    return compact_list(ids)


def chesscom_event_from_item(item):
    event = item.get("event") or item
    title = clean_text(event.get("name"))
    event_id = event.get("id")
    if not title or not event_id:
        return None
    slug = clean_text(event.get("slug")) or slugify(title)
    event_url = f"https://www.chess.com/events/info/{slug}"
    start_date = to_iso(parse_date_safely(event.get("startAt")))
    end_date = to_iso(parse_date_safely(event.get("endAt"))) or start_date
    player_count = event.get("playerCount")
    round_count = event.get("roundCount")
    streams = []
    for stream in event.get("streams") or []:
        duration = stream_duration_hours(stream)
        if duration is None or duration > 16:
            continue
        if not parse_date_safely(stream.get("startAt")):
            continue
        streams.append(stream)
    links = [
        {"label": "Chess.com event", "type": "official", "url": event_url},
        {"label": "Chess.com current events", "type": "source", "url": CHESSCOM_CURRENT_EVENTS_URL},
    ]
    for stream in streams:
        link = chesscom_stream_link(stream)
        if link:
            links.append(link)
    details = []
    if player_count:
        details.append(f"{player_count} listed players")
    if round_count:
        details.append(f"{round_count} rounds")
    if streams:
        details.append(f"{len(streams)} scheduled stream links")
    description = ". ".join(details) or "Listed on the Chess.com current events page."
    categories = infer_categories_from_text(title, event.get("featuredTitle"), event.get("eventType"), "broadcast", "online")
    return normalize_event({
        "id": f"chesscom-{event_id}",
        "title": title,
        "shortTitle": clean_text(event.get("featuredTitle")) or title,
        "startDate": start_date,
        "endDate": end_date,
        "timezone": "UTC",
        "locationName": "Online broadcast",
        "isOnline": True,
        "summary": f"Chess.com current event: {title}.",
        "description": description,
        "categories": categories,
        "playerIds": infer_player_ids_from_text(title, event.get("featuredTitle")),
        "channelIds": chesscom_channel_ids(event, streams),
        "primaryUrl": event_url,
        "links": links,
        "source": {"name": "chesscom", "url": CHESSCOM_CURRENT_EVENTS_URL, "confidence": "medium"},
    })


def fetch_chesscom_current_events(limit=150):
    events = []
    search_after = None
    headers = {
        "Origin": "https://www.chess.com",
        "Referer": CHESSCOM_CURRENT_EVENTS_URL,
    }
    while len(events) < limit:
        payload = {
            "searchFor": "",
            "sortBy": "relevance",
            "timeFilter": "current",
            "size": min(50, limit - len(events)),
            "featured": False,
            "includeSelfServe": False,
        }
        if search_after:
            payload["searchAfter"] = search_after
        data = request_json(CHESSCOM_EVENTS_API_URL, payload=payload, headers=headers)
        results = data.get("results") or []
        if not results:
            break
        for item in results:
            normalized = chesscom_event_from_item(item)
            if normalized:
                events.append(normalized)
        next_search_after = results[-1].get("searchAfter")
        if not next_search_after or next_search_after == search_after:
            break
        search_after = next_search_after
    return events[:limit]


def fetch_us_chess_plan_ahead():
    html = request_text(US_CHESS_PLAN_AHEAD_URL)
    if "Plan Ahead" not in html:
        raise SourceSkipped("US Chess page did not expose parseable Plan Ahead event rows")
    return []


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


def parse_html_cells(row_html):
    return [clean_text(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row_html, re.S | re.I)]


def parse_chess_results_players(html, event_id, source_url, max_players=300):
    table_match = re.search(r'<table class="CRs1"[^>]*>(.*?)</table>', html, re.S | re.I)
    if not table_match:
        return []
    header_map = {}
    players = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(1), re.S | re.I):
        cells = parse_html_cells(row)
        if not cells:
            continue
        if not header_map and any(cell.lower() in {"name", "nombre"} for cell in cells):
            for index, cell in enumerate(cells):
                key = cell.lower().replace(".", "").replace("-", "").replace(" ", "")
                if key in {"name", "nombre"}:
                    header_map["name"] = index
                elif key in {"fed", "federation", "federacion"}:
                    header_map["countryCode"] = index
                elif key in {"rtgi", "elo", "rating"}:
                    header_map["classicalRating"] = index
                elif key in {"fideid", "fide"}:
                    header_map["fideId"] = index
            continue
        if len(cells) < 4 or not cells[0].isdigit():
            continue
        name_index = header_map.get("name", 4)
        if name_index >= len(cells):
            continue
        raw_name = cells[name_index]
        name = display_player_name(raw_name)
        if not name or name.lower() in {"name", "nombre"} or name.isdigit():
            continue
        title_index = name_index - 1
        rating_index = header_map.get("classicalRating")
        fide_index = header_map.get("fideId")
        country_index = header_map.get("countryCode")
        if country_index is None:
            country_match = re.search(r'class="[^"]*\b([A-Z]{3})\b[^"]*"', row)
            country_code = country_match.group(1) if country_match else None
        else:
            country_code = cells[country_index] if country_index < len(cells) else None
        rating_text = cells[rating_index] if rating_index is not None and rating_index < len(cells) else ""
        fide_text = cells[fide_index] if fide_index is not None and fide_index < len(cells) else ""
        rating = int(rating_text) if rating_text.isdigit() and int(rating_text) > 0 else None
        title = cells[title_index] if 0 <= title_index < len(cells) and cells[title_index] else None
        looks_like_group = re.search(r"\b(memorial|memorijal|open|festival|junior|kadet|cadet|tournament)\b", name, re.I)
        if looks_like_group and not any([title, rating, fide_text.isdigit()]):
            continue
        player = {
            "eventId": event_id,
            "playerId": slugify(name),
            "name": name,
            "countryCode": country_code or None,
            "title": title,
            "fideId": fide_text if fide_text.isdigit() else None,
            "classicalRating": rating,
            "status": "confirmed",
            "source": {
                "name": "chess-results",
                "url": source_url,
                "confidence": "high",
            },
        }
        players.append(player)
        if len(players) >= max_players:
            break
    return players


def fetch_event_players_for_event(event):
    player_links = [link for link in event.get("links", []) if link.get("type") == "players" and link.get("url")]
    players = []
    seen = set()
    for link in player_links:
        url = link["url"]
        host = urlparse(url).netloc.lower()
        try:
            html = request_text(url, timeout=20)
        except (OSError, URLError, TimeoutError, ValueError) as exc:
            logging.error("player source failed for %s: %s", event["id"], exc)
            continue
        source_players = []
        if "chess-results.com" in host:
            source_players = parse_chess_results_players(html, event["id"], url)
        else:
            logging.info("No confirmed roster parser for %s", host)
        for player in source_players:
            key = (player["eventId"], player["playerId"])
            if key in seen:
                continue
            seen.add(key)
            players.append(player)
    return players


CURATED_TOP_PLAYER_ROSTERS = [
    {
        "eventTitleContains": "norway chess 2026",
        "excludeTitleContains": "women",
        "source": {
            "name": "official",
            "url": "https://norwaychess.no/en/2026/02/24/the-full-lineup-for-norway-chess-2026-announced/",
            "confidence": "high",
        },
        "players": [
            "Magnus Carlsen",
            "Vincent Keymer",
            "Alireza Firouzja",
            "Gukesh Dommaraju",
            "R Praggnanandhaa",
            "Wesley So",
        ],
    },
    {
        "eventTitleContains": "norway chess women 2026",
        "source": {
            "name": "official",
            "url": "https://norwaychess.no/en/2026/02/24/the-full-lineup-for-norway-chess-2026-announced/",
            "confidence": "high",
        },
        "players": [
            "Ju Wenjun",
            "Humpy Koneru",
            "Zhu Jiner",
            "Divya Deshmukh",
        ],
    },
    {
        "eventTitleContains": "super chess classic romania",
        "source": {
            "name": "chessbase",
            "url": "https://en.chessbase.com/post/super-classic-romania-2026-live",
            "confidence": "high",
        },
        "players": [
            "Fabiano Caruana",
            "Anish Giri",
            "Vincent Keymer",
            "Alireza Firouzja",
            "Wesley So",
            "R Praggnanandhaa",
            "Maxime Vachier-Lagrave",
            "Bogdan-Daniel Deac",
        ],
    },
    {
        "eventTitleContains": "superbet chess classic romania",
        "source": {
            "name": "chessbase",
            "url": "https://en.chessbase.com/post/super-classic-romania-2026-live",
            "confidence": "high",
        },
        "players": [
            "Fabiano Caruana",
            "Anish Giri",
            "Vincent Keymer",
            "Alireza Firouzja",
            "Wesley So",
            "R Praggnanandhaa",
            "Maxime Vachier-Lagrave",
            "Bogdan-Daniel Deac",
        ],
    },
]


def curated_player_record(event_id, player_name, source):
    player_id = slugify(player_name)
    return {
        "eventId": event_id,
        "playerId": player_id,
        "name": player_name,
        "countryCode": PLAYER_COUNTRY_CODES.get(player_id),
        "title": "GM",
        "fideId": None,
        "classicalRating": None,
        "status": "confirmed",
        "source": source,
    }


def curated_top_player_rosters(events):
    players = []
    seen = set()
    for event in events:
        title = event["title"].lower()
        for roster in CURATED_TOP_PLAYER_ROSTERS:
            include = roster["eventTitleContains"] in title
            exclude = roster.get("excludeTitleContains") and roster["excludeTitleContains"] in title
            if not include or exclude:
                continue
            for player_name in roster["players"]:
                player = curated_player_record(event["id"], player_name, roster["source"])
                key = (player["eventId"], player["playerId"])
                if key in seen:
                    continue
                seen.add(key)
                players.append(player)
    return players


def build_event_players(events_payload):
    players = []
    for event in events_payload["events"]:
        players.extend(fetch_event_players_for_event(event))
    players.extend(curated_top_player_rosters(events_payload["events"]))
    return {
        "generatedAt": iso_now(),
        "eventPlayerCount": len(players),
        "sources": [
            {
                "name": "chess-results",
                "status": "ok",
                "count": sum(1 for player in players if player["source"]["name"] == "chess-results"),
            },
            {
                "name": "curated",
                "status": "ok",
                "count": sum(1 for player in players if player["source"]["name"] in {"official", "chessbase"}),
            }
        ],
        "eventPlayers": players,
    }


def attach_confirmed_players(events_payload, event_players_payload):
    by_event = {}
    for player in event_players_payload["eventPlayers"]:
        by_event.setdefault(player["eventId"], []).append(player)
    for event in events_payload["events"]:
        confirmed = by_event.get(event["id"], [])
        event["confirmedPlayerIds"] = [player["playerId"] for player in confirmed]
        event["confirmedPlayers"] = [
            {
                "playerId": player["playerId"],
                "name": player["name"],
                "countryCode": player["countryCode"],
                "title": player["title"],
                "fideId": player["fideId"],
                "classicalRating": player["classicalRating"],
                "profileLinks": PLAYER_PROFILE_LINKS.get(player["playerId"], {}),
                "status": player["status"],
                "source": player["source"],
            }
            for player in confirmed
        ]
        event["playerIds"] = compact_list(event.get("playerIds", []) + event["confirmedPlayerIds"])
    return events_payload


def event_summaries_by_player(events_payload, event_players_payload):
    event_lookup = {event["id"]: event for event in events_payload["events"]}
    by_player = {}
    for player in event_players_payload["eventPlayers"]:
        event = event_lookup.get(player["eventId"])
        if not event:
            continue
        by_player.setdefault(player["playerId"], []).append({
            "eventId": event["id"],
            "title": event["title"],
            "status": event["status"],
            "startDate": event["startDate"],
            "endDate": event["endDate"],
            "locationName": event["locationName"],
            "isOnline": event["isOnline"],
            "categories": event["categories"],
            "primaryUrl": event["primaryUrl"],
            "source": player["source"],
        })
    return by_player


def build_confirmed_players_feed(events_payload, event_players_payload, events_by_player):
    players = {}
    for player in event_players_payload["eventPlayers"]:
        player_id = player["playerId"]
        existing = players.get(player_id, {})
        players[player_id] = {
            "id": player_id,
            "name": existing.get("name") or player["name"],
            "countryCode": existing.get("countryCode") or player["countryCode"],
            "title": existing.get("title") or player["title"],
            "fideId": existing.get("fideId") or player["fideId"],
            "classicalRating": max(filter(None, [existing.get("classicalRating"), player["classicalRating"]]), default=None),
            "profileLinks": PLAYER_PROFILE_LINKS.get(player_id, {}),
            "confirmedEventIds": [event["eventId"] for event in events_by_player.get(player_id, [])],
            "confirmedEvents": events_by_player.get(player_id, []),
        }
    ordered_players = sorted(players.values(), key=lambda player: player["name"])
    return {
        "generatedAt": iso_now(),
        "playerCount": len(ordered_players),
        "source": "confirmedEventRosters",
        "players": ordered_players,
    }


def collect_source(name, collector, source_results):
    try:
        events = collector()
        add_source_result(source_results, name, "ok", len(events))
        return events
    except SourceSkipped as exc:
        logging.info("%s source skipped: %s", name, exc)
        add_source_result(source_results, name, "skipped", 0, exc)
        return []
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        logging.error("%s source failed: %s", name, exc)
        add_source_result(source_results, name, "error", 0, exc)
        return []


def build_events():
    source_results = []
    events = []
    events.extend(collect_source("curated", curated_major_events, source_results))
    events.extend(collect_source("lichess", fetch_lichess_broadcasts, source_results))
    events.extend(collect_source("fide", fetch_fide_calendar, source_results))
    events.extend(collect_source("chessbase", fetch_chessbase_calendar, source_results))
    events.extend(collect_source("chessaround", fetch_chessaround_calendar, source_results))
    events.extend(collect_source("chessdom", fetch_chessdom_calendar, source_results))
    events.extend(collect_source("chessmix", fetch_chessmix_calendar, source_results))
    events.extend(collect_source("chesscalendar.net", fetch_chess_calendar_net, source_results))
    events.extend(collect_source("chesscom", fetch_chesscom_current_events, source_results))
    events.extend(collect_source("uschess", fetch_us_chess_plan_ahead, source_results))
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
        ("david-howell", "David Howell", "player", "GM David Howell's chess streams, commentary, and official site.", {"website": "https://www.howellchess.com/home", "lichess": "https://lichess.org/streamer/HowellHub", "twitch": "https://www.twitch.tv/howellhub"}),
        ("gingergm", "GingerGM", "creator", "GM Simon Williams' GingerGM chess lessons, videos, and streams.", {"website": "https://gingergm.com/", "lichess": "https://lichess.org/streamer/gingergm", "youtube": "https://www.youtube.com/c/GingerGM", "twitch": "https://www.twitch.tv/gingergm"}),
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


def player_entry(rank, name, country, country_code, events_by_player=None):
    events_by_player = events_by_player or {}
    player_id = slugify(name)
    confirmed_events = events_by_player.get(player_id, [])
    return {
        "id": player_id,
        "name": name,
        "country": country,
        "countryCode": country_code,
        "title": "GM",
        "fideId": None,
        "rank": rank,
        "classicalRating": None,
        "rapidRating": None,
        "blitzRating": None,
        "profileLinks": PLAYER_PROFILE_LINKS.get(player_id, {}),
        "confirmedEventIds": [event["eventId"] for event in confirmed_events],
        "confirmedEvents": confirmed_events,
    }


def build_player_feed(list_name, seeds, events_by_player=None):
    players = [player_entry(rank, *seed, events_by_player=events_by_player) for rank, seed in enumerate(seeds[:50], start=1)]
    return {
        "generatedAt": iso_now(),
        "list": list_name,
        "source": "manualSeed",
        "playerCount": len(players),
        "players": players,
    }


def main():
    events_payload = build_events()
    event_players_payload = build_event_players(events_payload)
    events_payload = attach_confirmed_players(events_payload, event_players_payload)
    events_by_player = event_summaries_by_player(events_payload, event_players_payload)
    write_json("events.json", events_payload)
    write_json("event_players.json", event_players_payload)
    write_json("channels.json", build_channels())
    write_json("players_confirmed.json", build_confirmed_players_feed(events_payload, event_players_payload, events_by_player))
    write_json("players_open.json", build_player_feed("top50_open", OPEN_PLAYER_SEEDS, events_by_player))
    write_json("players_women.json", build_player_feed("top50_women", WOMEN_PLAYER_SEEDS, events_by_player))


if __name__ == "__main__":
    main()

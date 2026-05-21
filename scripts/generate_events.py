import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
LICHESS_BROADCAST_URL = "https://lichess.org/api/broadcast"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ms_to_iso(ms):
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(value):
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def compact_list(values):
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def write_json(filename, payload):
    path = ROOT / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logging.info("Generated %s", filename)


def event_status(start_date, end_date):
    now = now_utc()
    start = parse_iso(start_date)
    end = parse_iso(end_date)
    if start and end and start <= now <= end:
        return "live"
    if end and end < now:
        return "completed"
    return "upcoming"


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_event(event):
    title = event.get("title") or "Chess Event"
    primary_url = event.get("primaryUrl") or ""
    links = event.get("links") or []
    normalized = {
        "id": event.get("id") or slugify(title),
        "title": title,
        "shortTitle": event.get("shortTitle") or title,
        "status": event.get("status") or event_status(event.get("startDate"), event.get("endDate")),
        "startDate": event.get("startDate"),
        "endDate": event.get("endDate"),
        "timezone": event.get("timezone") or "UTC",
        "locationName": event.get("locationName") or ("Online" if event.get("isOnline") else "TBD"),
        "isOnline": bool(event.get("isOnline")),
        "summary": event.get("summary") or "",
        "description": event.get("description") or event.get("summary") or "",
        "categories": compact_list(event.get("categories") or []),
        "playerIds": compact_list(event.get("playerIds") or []),
        "channelIds": compact_list(event.get("channelIds") or []),
        "primaryUrl": primary_url,
        "links": links,
    }
    if normalized["status"] not in {"upcoming", "live", "completed"}:
        normalized["status"] = event_status(normalized["startDate"], normalized["endDate"])
    return normalized


def curated_events():
    return [
        normalize_event({
            "id": "titled-tuesday",
            "title": "Titled Tuesday",
            "shortTitle": "Titled Tuesday",
            "startDate": "2026-01-01T17:00:00Z",
            "endDate": "2026-01-01T20:00:00Z",
            "timezone": "Europe/Zurich",
            "locationName": "Online",
            "isOnline": True,
            "summary": "Weekly Chess.com titled-player online event.",
            "description": "Titled Tuesday is a regular online event for titled chess players.",
            "categories": ["online", "blitz"],
            "channelIds": ["chesscom"],
            "primaryUrl": "https://www.chess.com/article/view/titled-tuesday",
            "links": [
                {"label": "Chess.com event info", "type": "official", "url": "https://www.chess.com/article/view/titled-tuesday"},
                {"label": "Chess.com TV", "type": "watch", "url": "https://www.chess.com/tv"},
            ],
        }),
        normalize_event({
            "id": "norway-chess-2026",
            "title": "Norway Chess 2026",
            "shortTitle": "Norway Chess",
            "startDate": "2026-05-25T13:00:00Z",
            "endDate": "2026-06-05T21:00:00Z",
            "timezone": "Europe/Oslo",
            "locationName": "Oslo, Norway",
            "isOnline": False,
            "summary": "Elite over-the-board tournament hosted by Norway Chess.",
            "description": "Norway Chess 2026 is scheduled for 25 May to 5 June in Oslo, with open and women's events.",
            "categories": ["classical", "elite", "otb"],
            "channelIds": ["norway-chess"],
            "primaryUrl": "https://norwaychess.no/en/schedule-2026/",
            "links": [
                {"label": "Official schedule", "type": "official", "url": "https://norwaychess.no/en/schedule-2026/"},
                {"label": "Norway Chess", "type": "watch", "url": "https://norwaychess.no/en/"},
            ],
        }),
    ]


def infer_categories(info, name):
    text = " ".join(str(info.get(key, "")) for key in ("fideTC", "format", "tc")) + " " + name
    lower = text.lower()
    categories = ["broadcast"]
    if "rapid" in lower:
        categories.append("rapid")
    if "blitz" in lower:
        categories.append("blitz")
    if "classical" in lower or "standard" in lower:
        categories.append("classical")
    if "women" in lower or "womens" in lower or "women's" in lower:
        categories.append("women")
    if "junior" in lower or "youth" in lower:
        categories.append("junior")
    return compact_list(categories)


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
        "categories": infer_categories(info, name),
        "channelIds": ["lichess"],
        "primaryUrl": url,
        "links": links,
    })


def fetch_lichess_broadcasts(limit=15):
    try:
        request = Request(LICHESS_BROADCAST_URL, headers={"Accept": "application/x-ndjson"})
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except (OSError, URLError, TimeoutError) as exc:
        logging.error("Could not fetch Lichess broadcasts: %s", exc)
        return []

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


def build_events():
    events = curated_events()
    existing_ids = {event["id"] for event in events}
    for event in fetch_lichess_broadcasts():
        if event["id"] not in existing_ids:
            events.append(event)
            existing_ids.add(event["id"])
    events.sort(key=lambda event: event.get("startDate") or "9999-12-31T23:59:59Z")
    return {"generatedAt": iso_now(), "eventCount": len(events), "events": events}


def build_channels():
    channels = [
        ("chesscom", "Chess.com", "officialChannel", "Official Chess.com coverage, events, and broadcasts.", {"website": "https://www.chess.com/", "youtube": "https://www.youtube.com/@chesscom", "twitch": "https://www.twitch.tv/chess"}),
        ("lichess", "Lichess", "officialChannel", "Official Lichess broadcasts and community chess coverage.", {"website": "https://lichess.org/", "broadcasts": "https://lichess.org/broadcast", "youtube": "https://www.youtube.com/@lichessdotorg"}),
        ("fide", "FIDE", "officialChannel", "Official International Chess Federation news and event coverage.", {"website": "https://www.fide.com/", "youtube": "https://www.youtube.com/@FIDE_chess"}),
        ("saint-louis-chess-club", "Saint Louis Chess Club", "officialChannel", "Tournament broadcasts and educational chess content from Saint Louis.", {"website": "https://saintlouischessclub.org/", "youtube": "https://www.youtube.com/@STLChessClub", "twitch": "https://www.twitch.tv/stlchessclub"}),
        ("freestyle-chess", "Freestyle Chess", "officialChannel", "Official Freestyle Chess tournament coverage.", {"website": "https://www.freestyle-chess.com/", "youtube": "https://www.youtube.com/@FreestyleChess"}),
        ("take-take-take", "Take Take Take", "creator", "Chess media and commentary platform focused on elite events.", {"website": "https://www.taketaketake.com/", "youtube": "https://www.youtube.com/@TakeTakeTakeApp"}),
        ("norway-chess", "Norway Chess", "officialChannel", "Official Norway Chess tournament news, schedules, and broadcasts.", {"website": "https://norwaychess.no/en/", "youtube": "https://www.youtube.com/@NorwayChess"}),
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

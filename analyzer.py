from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

VERDICT_SAFE = "Possibly safe"
VERDICT_CAUTION = "Hmmmmm...."
VERDICT_DANGER = "Don't ffs!"

# Backwards-compatible short names used by the GUI and tests.
SAFE = VERDICT_SAFE
CAUTION = VERDICT_CAUTION
DANGER = VERDICT_DANGER

THRESHOLDS = {
    "safe_max": 24,
    "danger_min": 60,
    "strong_floor": 30, # minimum score when rip-bait wording is present
}

WEIGHTS = {
    "blocklist": 100,
    "strong_keyword": 35,
    "weak_keyword": 10,
    "author_keyword_share": 20,
    "new_world": 10,
    "old_world": -10,
    "low_engagement": 10,       # lots of visits, almost no favs = sus
    "good_engagement": -5,
    "single_world_author": 10,
    "established_author": -15,
    "empty_description": 5,
    "shouty_name": 5,
    "approved_tag": -15,
}

NEW_WORLD_DAYS = 14
OLD_WORLD_DAYS = 365

LOW_ENGAGEMENT_MIN_VISITS = 1000
LOW_ENGAGEMENT_MAX_RATIO = 0.005
GOOD_ENGAGEMENT_MIN_VISITS = 500
GOOD_ENGAGEMENT_MIN_RATIO = 0.03

AUTHOR_SHARE_MIN_OTHER_WORLDS = 2
AUTHOR_SHARE_THRESHOLD = 0.5
ESTABLISHED_AUTHOR_MIN_WORLDS = 5
ESTABLISHED_AUTHOR_MIN_FAVORITES = 1000

LOW_CONFIDENCE_MAX_VISITS = 100

# Note to the next developer:
# If you're feeling like writing regex and suffering with more of the wording, make sure to increment the following counter.

# timesIHadToRewriteThis: 13
STRONG_KEYWORD_PATTERNS = [
    r"\bfree\s+(?:public\s+)?(?:avatars?|avis?|avies)\b",
    r"\bpublic\s+(?:avatars?|avis?)\b",
    r"\bavatars?\s+(?:pedestals?|dump|leaks?|stash|vault|rip(?:s|ped|per)?|thief)\b",
    r"\b(?:ripped|ripping|leaked|stolen)\s+(?:avatars?|avis?)\b",
    r"\bavi\s+(?:dump|leaks?|stash|rip(?:s|ped)?)\b",
    r"\b(?:copy|clone|steal)\s+(?:any|all|every|other)?\s*(?:avatars?|avis?)\b",
]
WEAK_KEYWORD_PATTERNS = [
    r"\bavatars?\b",
    r"\bavis?\b",
    r"\bpedestals?\b",
]
STRONG_KEYWORD_REGEXES = [re.compile(pattern, re.IGNORECASE) for pattern in STRONG_KEYWORD_PATTERNS]
WEAK_KEYWORD_REGEXES = [re.compile(pattern, re.IGNORECASE) for pattern in WEAK_KEYWORD_PATTERNS]

AUTHOR_TAG_PREFIX = "author_tag_"

@dataclass
class Signal:
    #One reason that moved the score up or down

    label: str
    points: int
    detail: str


@dataclass
class Report:
    world_id: str
    name: str
    author_name: str
    author_id: str
    score: int
    verdict: str
    confidence: str
    signals: list[Signal] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    author_worlds: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# Helper funcs
def _make_signal(weight_key: str, label: str, detail: str) -> Signal:
    return Signal(label=label, points=WEIGHTS[weight_key], detail=detail)


def _parse_timestamp(timestamp) -> Optional[datetime]:
    #Parse VRChat's ISO timestamps ('...Z'), returns None for missing/odd values like...well 'none'.
    if not timestamp or not isinstance(timestamp, str):
        return None
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _days_between(earlier: Optional[datetime], later: datetime) -> Optional[int]:
    if earlier is None:
        return None
    return (later - earlier).days


def _author_tags_as_text(tags) -> str:
    readable_tags = []
    for tag in tags or []:
        if isinstance(tag, str) and tag.startswith(AUTHOR_TAG_PREFIX):
            readable_tags.append(tag[len(AUTHOR_TAG_PREFIX):].replace("_", " "))
    return " ".join(readable_tags)


def _searchable_text(world: dict) -> str:
    name = world.get("name") or ""
    description = world.get("description") or ""
    tag_text = _author_tags_as_text(world.get("tags"))
    return " ".join([name, description, tag_text])


def _first_match_of_each(text: str, regexes: list[re.Pattern]) -> list[str]:
    found = []
    for regex in regexes:
        match = regex.search(text)
        if match:
            found.append(match.group(0))
    return found


def find_keyword_matches(world: dict) -> tuple[list[str], list[str]]:
    #Return (strong_matches, weak_matches) found in the world's name, description, tags and whatev
    text = _searchable_text(world)
    strong_matches = _first_match_of_each(text, STRONG_KEYWORD_REGEXES)
    weak_matches = _first_match_of_each(text, WEAK_KEYWORD_REGEXES)
    return strong_matches, weak_matches


def has_avatar_wording(world: dict) -> bool:
    strong_matches, weak_matches = find_keyword_matches(world)
    return bool(strong_matches or weak_matches)


def _unique_lowercase(matches: list[str]) -> str:
    return ", ".join(sorted({match.lower() for match in matches}))


def _clamp(value: int, lowest: int, highest: int) -> int:
    return max(lowest, min(highest, value))


# blocklist stuff
def load_blocklist(path: Optional[Path]) -> dict:
    empty_blocklist = {"worlds": [], "authors": []}
    if not path:
        return empty_blocklist
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return empty_blocklist
    return {
        "worlds": list(data.get("worlds", [])),
        "authors": list(data.get("authors", [])),
    }


def add_to_blocklist(path: Path, world_id: str, author_id: str) -> None:
    blocklist = load_blocklist(path)
    if world_id and world_id not in blocklist["worlds"]:
        blocklist["worlds"].append(world_id)
    if author_id and author_id not in blocklist["authors"]:
        blocklist["authors"].append(author_id)
    Path(path).write_text(json.dumps(blocklist, indent=2))


def _is_blocklisted(world_id: str, author_id: str, blocklist: dict) -> bool:
    return world_id in blocklist["worlds"] or author_id in blocklist["authors"]


# signal checks
def _blocklist_signals(world_id: str, author_id: str, blocklist: dict) -> list[Signal]:
    if world_id in blocklist["worlds"]:
        return [_make_signal("blocklist", "On your blocklist", "This world ID is on your local blocklist.")]
    if author_id in blocklist["authors"]:
        return [_make_signal("blocklist", "Author on your blocklist", "The author is on your local blocklist.")]
    return []


def _keyword_signals(strong_matches: list[str], weak_matches: list[str]) -> list[Signal]:
    if strong_matches:
        detail = "Name/description/tags contain: " + _unique_lowercase(strong_matches)
        return [_make_signal("strong_keyword", "Rip-bait wording", detail)]
    if weak_matches:
        detail = (
            "Mentions: " + _unique_lowercase(weak_matches)
            + " (many legit avatar-testing worlds exist too)"
        )
        return [_make_signal("weak_keyword", "Avatar-related wording", detail)]
    return []


def _author_history_signals(world_id: str, author_worlds: list[dict]) -> list[Signal]:
    # Does the author specialise in avatar worlds?
    if not author_worlds:
        return []

    signals = []
    other_worlds = [world for world in author_worlds if world.get("id") != world_id]

    if len(other_worlds) >= AUTHOR_SHARE_MIN_OTHER_WORLDS:
        avatar_world_count = sum(1 for world in other_worlds if has_avatar_wording(world))
        avatar_share = avatar_world_count / len(other_worlds)
        if avatar_share >= AUTHOR_SHARE_THRESHOLD:
            detail = f"{avatar_world_count} of {len(other_worlds)} other worlds by this author have avatar wording."
            signals.append(_make_signal("author_keyword_share", "Author specialises in avatar worlds", detail))

    total_favorites = sum(int(world.get("favorites") or 0) for world in author_worlds)
    if len(author_worlds) <= 1:
        signals.append(_make_signal(
            "single_world_author", "Single-world author", "No other public worlds found from this author."))
    elif len(author_worlds) >= ESTABLISHED_AUTHOR_MIN_WORLDS and total_favorites >= ESTABLISHED_AUTHOR_MIN_FAVORITES:
        detail = f"{len(author_worlds)} public worlds, {total_favorites:,} favorites in total."
        signals.append(_make_signal("established_author", "Established author", detail))

    return signals


def _age_signals(age_days: Optional[int]) -> list[Signal]:
    if age_days is None:
        return []
    if age_days < NEW_WORLD_DAYS:
        return [_make_signal("new_world", "Brand-new world", f"Created {age_days} day(s) ago.")]
    if age_days > OLD_WORLD_DAYS:
        return [_make_signal("old_world", "Long-lived world", f"Created {age_days // 365}+ year(s) ago.")]
    return []


def _engagement_signals(visit_count: int, favorite_count: int, favorite_ratio: float) -> list[Signal]:
    has_many_visits_few_favorites = (
        visit_count >= LOW_ENGAGEMENT_MIN_VISITS and favorite_ratio < LOW_ENGAGEMENT_MAX_RATIO
    )
    if has_many_visits_few_favorites:
        detail = f"{visit_count:,} visits but only {favorite_count:,} favorites ({favorite_ratio:.2%})."
        return [_make_signal("low_engagement", "Visits without favorites (kinda sussy)", detail)]

    has_healthy_engagement = (
        visit_count >= GOOD_ENGAGEMENT_MIN_VISITS and favorite_ratio >= GOOD_ENGAGEMENT_MIN_RATIO
    )
    if has_healthy_engagement:
        return [_make_signal("good_engagement", "Healthy engagement", f"{favorite_ratio:.1%} of visitors favorited it.")]

    return []


def _presentation_signals(world: dict) -> list[Signal]:
    signals = []
    description = (world.get("description") or "").strip()
    name = world.get("name") or ""

    if not description:
        signals.append(_make_signal("empty_description", "No description", "The author wrote no description."))
    if re.search(r"\bFREE\b", name):  # case-sensitive on purpose: only shouting counts
        signals.append(_make_signal("shouty_name", "Shouty name", "Name uses ALL-CAPS 'FREE'."))
    return signals


def _platform_trust_signals(world: dict) -> list[Signal]:
    if "system_approved" in (world.get("tags") or []):
        return [_make_signal("approved_tag", "VRChat-approved tag", "Carries the system_approved tag.")]
    return []


# stats and scording
def _verdict_for_score(score: int) -> str:
    if score >= THRESHOLDS["danger_min"]:
        return VERDICT_DANGER
    if score > THRESHOLDS["safe_max"]:
        return VERDICT_CAUTION
    return VERDICT_SAFE


def _confidence_level(visit_count: int, author_worlds: list[dict]) -> str:
    #Never 'High': this is a assumption. Low when we have little data to go on.
    if visit_count < LOW_CONFIDENCE_MAX_VISITS or not author_worlds:
        return "Low"
    return "Medium"


def _build_stats(world: dict, favorite_ratio: float, age_days: Optional[int],
                 days_since_update: Optional[int]) -> dict:
    tags = world.get("tags") or []
    platforms = sorted({
        package.get("platform")
        for package in world.get("unityPackages", [])
        if isinstance(package, dict) and package.get("platform")
    })
    public_tags = [tag for tag in tags if not str(tag).startswith(AUTHOR_TAG_PREFIX)]
    author_tags = [tag[len(AUTHOR_TAG_PREFIX):] for tag in tags if str(tag).startswith(AUTHOR_TAG_PREFIX)]

    return {
        "visits": int(world.get("visits") or 0),
        "favorites": int(world.get("favorites") or 0),
        "fav_ratio": favorite_ratio,
        "popularity": world.get("popularity"),
        "heat": world.get("heat"),
        "occupants": world.get("occupants"),
        "capacity": world.get("capacity"),
        "age_days": age_days,
        "days_since_update": days_since_update,
        "version": world.get("version"),
        "platforms": platforms,
        "tags": public_tags,
        "author_tags": author_tags,
    }


def _summarize_author_worlds(author_worlds: list[dict]) -> list[dict]:
    return [
        {
            "id": world.get("id"),
            "name": world.get("name"),
            "visits": int(world.get("visits") or 0),
            "favorites": int(world.get("favorites") or 0),
        }
        for world in author_worlds
    ]


# ---- main entry point ----------------------------------------------------------------------
def analyze(world: dict, author_worlds: Optional[list[dict]] = None,
            blocklist: Optional[dict] = None, now: Optional[datetime] = None) -> Report:
    """Score one world. `author_worlds` and `blocklist` are optional extra context."""
    now = now or datetime.now(timezone.utc)
    author_worlds = author_worlds or []
    blocklist = blocklist or {"worlds": [], "authors": []}
    notes: list[str] = []

    world_id = world.get("id", "")
    author_id = world.get("authorId", "")
    visit_count = int(world.get("visits") or 0)
    favorite_count = int(world.get("favorites") or 0)
    favorite_ratio = favorite_count / visit_count if visit_count else 0.0
    age_days = _days_between(_parse_timestamp(world.get("created_at")), now)
    days_since_update = _days_between(_parse_timestamp(world.get("updated_at")), now)
    strong_matches, weak_matches = find_keyword_matches(world)

    signals: list[Signal] = []
    signals += _blocklist_signals(world_id, author_id, blocklist)
    signals += _keyword_signals(strong_matches, weak_matches)
    signals += _author_history_signals(world_id, author_worlds)
    signals += _age_signals(age_days)
    signals += _engagement_signals(visit_count, favorite_count, favorite_ratio)
    signals += _presentation_signals(world)
    signals += _platform_trust_signals(world)

    if not author_worlds:
        notes.append("Author's other worlds could not be loaded; author checks were skipped.")
    release_status = world.get("releaseStatus")
    if release_status not in (None, "public"):
        notes.append(f"Release status is '{release_status}', so metadata may be limited.")

    score = _clamp(sum(signal.points for signal in signals), 0, 100)
    if _is_blocklisted(world_id, author_id, blocklist):
        score = 100  # your own blocklist overrides every trust signal
    if strong_matches and score < THRESHOLDS["strong_floor"]:
        score = THRESHOLDS["strong_floor"]  # never call rip-bait wording "safe"
        notes.append("Score raised to the minimum for worlds with rip-bait wording.")

    return Report(
        world_id=world_id,
        name=world.get("name", "?"),
        author_name=world.get("authorName", "?"),
        author_id=author_id,
        score=score,
        verdict=_verdict_for_score(score),
        confidence=_confidence_level(visit_count, author_worlds),
        signals=sorted(signals, key=lambda signal: -abs(signal.points)),
        stats=_build_stats(world, favorite_ratio, age_days, days_since_update),
        author_worlds=_summarize_author_worlds(author_worlds),
        notes=notes,
    )


# ---- text rendering ----------------------------------------------------------------------------
def _format_header(report: Report) -> list[str]:
    return [
        report.name,
        f"by {report.author_name} ({report.author_id})",
        f"World ID: {report.world_id}",
        "",
        f"VERDICT: {report.verdict}",
        f"Risk score: {report.score}/100   Confidence: {report.confidence}",
        "",
    ]


def _format_metadata(report: Report) -> list[str]:
    stats = report.stats
    platforms = ", ".join(stats["platforms"]) or "unknown"
    author_tags = ", ".join(stats["author_tags"]) or "-"
    return [
        "-- Metadata --",
        f"Visits: {stats['visits']:,}   Favorites: {stats['favorites']:,} ({stats['fav_ratio']:.2%})",
        (f"Popularity: {stats['popularity']}   Heat: {stats['heat']}   "
         f"Online now: {stats['occupants']}   Capacity: {stats['capacity']}"),
        (f"Age: {stats['age_days']} days   Last update: {stats['days_since_update']} days ago   "
         f"Version: {stats['version']}"),
        f"Platforms: {platforms}",
        f"Tags: {author_tags}",
        "",
    ]


def _format_signals(report: Report) -> list[str]:
    lines = ["-- Why --"]
    if not report.signals:
        lines.append("No notable signals either way.")
        return lines

    for signal in report.signals:
        sign = "+" if signal.points > 0 else ""
        lines.append(f"[{sign}{signal.points}] {signal.label}: {signal.detail}")
    return lines


def _format_notes(report: Report) -> list[str]:
    if not report.notes:
        return []
    return ["", "-- Notes --"] + [f"* {note}" for note in report.notes]


def render_text(report: Report) -> str:
    lines = _format_header(report) + _format_metadata(report) + _format_signals(report) + _format_notes(report)
    lines += ["", "Assumption only! VRChat's API has no reviews or report data. Use your judgment! This tools is to be used as a tip."]
    return "\n".join(lines)


# Netowrkz
def fetch_and_analyze(client, world_id: str, blocklist: Optional[dict] = None) -> Report:
    # Pull the world and the author's other worlds from the API and analyze them too
    world = client.get_world(world_id)
    author_id = world.get("authorId", "")
    author_worlds = client.get_author_worlds(author_id) if author_id else []
    return analyze(world, author_worlds, blocklist)

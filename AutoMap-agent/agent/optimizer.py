"""Choose experiment presets, posting hours, and reusable topic metadata.

AgentOptimizer reads AgentDB performance/history to balance preset exploration
and exploitation, prefer useful posting hours, and find competitor inspiration.
Randomness can be injected for reproducible tests. Pure helpers calculate video
scores, infer numeric label suffixes, and build descriptions/hashtags.
This module does not render files or call external APIs: collection lives in
``youtube_intel.py`` and candidate execution lives in ``video_generation.py``.
Changes to score weights affect both ranking and future experiment choices."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

from .db import AgentDB


@dataclass(frozen=True)
class EffectArm:
    """One experimental preset combining map style and video camera/reveal animations."""

    name: str
    description: str
    map_type: str
    zoom_enabled: bool
    reveal_enabled: bool
    zoom_out_enabled: bool
    intro_fade: bool
    subscribe_badge: bool


EFFECT_ARMS: tuple[EffectArm, ...] = (
    EffectArm(
        name="europe_zoom_reveal_fx",
        description="Europe map + smooth zoom + 3-dot black reveal + shake/glitch + zoom out",
        map_type="default",
        zoom_enabled=True,
        reveal_enabled=True,
        zoom_out_enabled=True,
        intro_fade=True,
        subscribe_badge=True,
    ),
    EffectArm(
        name="europe_zoom_only",
        description="Europe map + smooth zoom + 3-dot reveal + zoom out",
        map_type="default",
        zoom_enabled=True,
        reveal_enabled=True,
        zoom_out_enabled=True,
        intro_fade=True,
        subscribe_badge=True,
    ),
    EffectArm(
        name="europe_baseline",
        description="Europe map clean static view (no zoom/reveal)",
        map_type="default",
        zoom_enabled=False,
        reveal_enabled=False,
        zoom_out_enabled=False,
        intro_fade=True,
        subscribe_badge=True,
    ),
    EffectArm(
        name="europe_flags_reveal_fx",
        description="Europe flags + smooth zoom + 3-dot reveal + shake/glitch + zoom out",
        map_type="flag",
        zoom_enabled=True,
        reveal_enabled=True,
        zoom_out_enabled=True,
        intro_fade=True,
        subscribe_badge=True,
    ),
    EffectArm(
        name="world_countries_reveal_fx",
        description="World Countries flags + zoom + 3-dot reveal + shake/glitch + zoom out",
        map_type="world_countries",
        zoom_enabled=True,
        reveal_enabled=True,
        zoom_out_enabled=True,
        intro_fade=True,
        subscribe_badge=True,
    ),
)

ARMS_BY_NAME: dict[str, EffectArm] = {arm.name: arm for arm in EFFECT_ARMS}

_UNIT_HINTS: tuple[tuple[str, str], ...] = (
    (r"\b(percent|percentage|%|forest coverage|literacy rate|unemployment rate|tax rate)\b", "%"),
    (r"\b(speed limit|km/h|kilometers per hour|kilometres per hour|fastest train|top speed)\b", " km/h"),
    (r"\b(internet speed|download speed|broadband|mbps)\b", " Mbps"),
    (r"\b(minute|minutes|commute time|lunch break|screen time.*min)\b", " min"),
    (r"\b(hour|hours|sleep|sunshine hours|work per week)\b", "h"),
    (r"\b(second|seconds)\b", "s"),
    (r"\b(centimeter|centimeters|centimetre|centimetres|average height|how tall are)\b", "cm"),
    (r"\b(kilometer|kilometers|kilometre|kilometres|longest river|coastline|border length)\b", " km"),
    (r"\b(meter|meters|metre|metres|tallest mountain|highest peak|tallest building|tallest tower|deepest lake|waterfall|elevation|altitude|depth|how deep|how tall is the tallest)\b", "m"),
    (r"\b(temperature|celsius|warmest|coldest|hottest)\b", "°C"),
    (r"\b(kilogram|kilograms|weight|kg)\b", "kg"),
    (r"\b(liter|liters|litre|litres|alcohol consumption|beer consumption|wine consumption)\b", "L"),
    (r"\b(euro|eur)\b", "€"),
    (r"\b(dollar|usd|salary|wage|cost|price|gdp per capita|rent)\b", "$"),
)


def compute_video_score(
    views_24h: int | None,
    views_72h: int | None,
    avg_view_pct: float | None,
    likes_72h: int | None = None,
    *,
    comments: int | None = None,
    shares: int | None = None,
    subscribers_gained: int | None = None,
    subscribers_lost: int | None = None,
) -> float | None:
    """Compute a single comparable quality score combining reach, retention, and viewer engagement."""
    views = views_72h if views_72h is not None else views_24h
    if views is None:
        return None
    retention = float(avg_view_pct) if avg_view_pct is not None else 60.0
    retention_factor = max(0.15, min(2.2, retention / 100.0))

    safe_views = max(1.0, float(views))
    like_rate = float(likes_72h or 0) / safe_views
    comment_rate = float(comments or 0) / safe_views
    share_rate = float(shares or 0) / safe_views
    net_subs = max(0, int(subscribers_gained or 0) - int(subscribers_lost or 0))
    sub_rate = float(net_subs) / safe_views

    engagement_bonus = 1.0 + min(
        0.9,
        like_rate * 4.0 + comment_rate * 12.0 + share_rate * 16.0 + sub_rate * 20.0,
    )
    return round(float(views) * retention_factor * engagement_bonus, 2)


def infer_label_suffix(topic: str, mode: str) -> str:
    """Infer an optional unit suffix (e.g. %, m, $) for numeric map topics."""
    if mode != "number":
        return ""
    low = (topic or "").lower()
    for pattern, suffix in _UNIT_HINTS:
        if re.search(pattern, low):
            return suffix
    return ""


def normalize_topic_key(topic: str) -> str:
    """Normalize a topic string for duplicate comparison."""
    return re.sub(r"[^a-z0-9]+", " ", str(topic or "").lower()).strip()


class AgentOptimizer:
    """Chooses A/B effect arms, posting hours, and fresh topics from experiment history."""

    def __init__(self, db: AgentDB, *, epsilon: float = 0.20, rng: random.Random | None = None) -> None:
        self.db = db
        self.epsilon = max(0.0, min(1.0, float(epsilon)))
        self.rng = rng or random.Random()

    def choose_arm(self) -> tuple[EffectArm, str]:
        """Select an effect arm using epsilon-greedy exploration vs exploitation."""
        leaderboard = self.db.get_arm_leaderboard()
        by_name = {row["arm"]: row for row in leaderboard}

        # Ensure every arm gets at least 1 cold-start measurement before exploiting.
        untested = [
            arm
            for arm in EFFECT_ARMS
            if arm.name not in by_name or int(by_name[arm.name].get("measured_runs") or 0) == 0
        ]
        if untested:
            chosen = untested[0]
            return chosen, f"A/B cold-start test of '{chosen.name}' ({chosen.description})"

        # Explore with probability epsilon (preferring least-tested arm).
        if self.rng.random() < self.epsilon:
            chosen = min(
                EFFECT_ARMS,
                key=lambda arm: int(by_name[arm.name].get("total_runs") or 0),
            )
            return chosen, f"A/B exploration (20%): testing '{chosen.name}' to keep learning"

        # Exploit the highest average score arm.
        best_arm = max(
            EFFECT_ARMS,
            key=lambda arm: float(by_name[arm.name].get("avg_score") or 0.0),
        )
        best_row = by_name[best_arm.name]
        avg_views = int(round(float(best_row.get("avg_views") or 0)))
        avg_ret = round(float(best_row.get("avg_retention") or 0), 1)
        return (
            best_arm,
            f"A/B winner (80%): '{best_arm.name}' leads with avg {avg_views} views, {avg_ret}% retention",
        )

    def choose_posting_hours(self, posts_per_day: int = 2, fallback_hours: list[int] | None = None) -> list[int]:
        """Pick optimal posting hours (separated by >= 4h) from measured YouTube Analytics history."""
        count = max(1, min(6, int(posts_per_day)))
        defaults = [int(h) % 24 for h in (fallback_hours or [14, 20])]
        while len(defaults) < count:
            defaults.append((defaults[-1] + 5) % 24)

        perf = self.db.get_hour_performance()
        total_measured = sum(int(row["runs"]) for row in perf)
        if total_measured < 10 or len(perf) < count:
            return sorted(defaults[:count])

        picked: list[int] = []
        for row in perf:
            hour = int(row["posting_hour"]) % 24
            if all(min(abs(hour - p), 24 - abs(hour - p)) >= 4 for p in picked):
                picked.append(hour)
            if len(picked) == count:
                break

        for fallback in defaults:
            if len(picked) >= count:
                break
            if fallback not in picked:
                picked.append(fallback)
        return sorted(picked[:count])

    def pick_competitor_inspiration(self) -> dict[str, Any] | None:
        """Pick the highest outlier_ratio unused competitor video."""
        outliers = self.db.list_competitor_outliers(limit=10, unused_only=True, min_ratio=1.5)
        if not outliers:
            return None
        recent_keys = {normalize_topic_key(t) for t in self.db.list_recent_topics(limit=60)}
        for candidate in outliers:
            if normalize_topic_key(candidate.get("title", "")) not in recent_keys:
                return candidate
        return outliers[0]

    def clean_competitor_title_to_topic(self, title: str) -> str:
        """Strip hashtags and normalize a competitor title to match IDEA_EXAMPLES style."""
        cleaned = re.sub(r"#\w+", "", str(title or "")).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|:•?!")
        if not cleaned:
            return ""
        # Replace trailing regional suffixes like "in Europe" / "by country" with "in your country".
        cleaned = re.sub(
            r"\b(?:in\s+europe|in\s+each\s+country|by\s+country|per\s+country)\s*$",
            "in your country",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        low = cleaned.lower()
        if not re.match(r"^(what|how|which|where|who)\b", low):
            if re.match(r"^(most|biggest|largest|smallest|highest|lowest|average|best|worst|top)\b", low):
                cleaned = f"What is the {cleaned[0].lower()}{cleaned[1:]}"
            elif re.match(r"^(number of|count of)\b", low):
                cleaned = f"How many {cleaned.split(' ', 2)[-1]}"
            else:
                cleaned = f"What is {cleaned[0].lower()}{cleaned[1:]}"
        if "in your country" not in cleaned.lower():
            cleaned = f"{cleaned} in your country"
        if len(cleaned) > 95:
            cleaned = cleaned[:95].rsplit(" ", 1)[0].strip()
        if not cleaned.endswith("?"):
            cleaned = f"{cleaned}?"
        return cleaned


_TAG_STOPWORDS: frozenset[str] = frozenset({
    "what", "which", "where", "when", "who", "how", "many", "much", "does", "do",
    "did", "is", "are", "was", "were", "be", "been", "being", "the", "a", "an",
    "in", "on", "at", "of", "to", "for", "by", "per", "with", "from", "into",
    "your", "our", "their", "country", "countries", "nation", "nations", "most",
    "least", "more", "less", "there", "have", "has", "had", "must", "can", "will",
    "people", "person", "year", "years", "day", "days", "week", "month", "ever",
    "first", "last", "only", "still", "every", "each", "than", "that", "this",
    "these", "those", "and", "or", "but", "not", "all", "any", "get", "make",
})

_THEME_HASHTAGS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"\b(thunderstorm|storm|rain|snow|weather|climate|temperature|warmest|coldest|sunny|wind)", ("weather", "climate")),
    (r"\b(mountain|peak|lake|river|forest|island|sea|ocean|volcano|desert|nature)", ("nature", "earth")),
    (r"\b(salary|wage|gdp|cost|price|money|rich|poor|income|tax|rent|economy|euro|dollar)", ("economy", "finance")),
    (r"\b(food|drink|coffee|tea|beer|wine|restaurant|mcdonald|pizza|cuisine|dish|meal)", ("food", "culture")),
    (r"\b(sport|football|soccer|olympic|medal|tennis|basketball|hockey|athlete)", ("sports",)),
    (r"\b(car|speed|drive|driving|road|train|flight|airport|transport)", ("travel", "cars")),
    (r"\b(school|universit|student|education|study|literacy|book|language)", ("education",)),
    (r"\b(history|war|empire|ancient|castle|unesco|heritage|king|founded)", ("history",)),
    (r"\b(internet|tech|phone|gaming|gamer|social|youtube|netflix|robot)", ("tech",)),
    (r"\b(bear|wolf|wolves|animal|dog|cat|pet|wildlife|bird|shark)", ("animals", "wildlife")),
    (r"\b(population|cit|capital|border|landlocked|density|age|life|height)", ("worldfacts", "statistics")),
)


def generate_topic_hashtags(topic: str, map_type: str = "default", max_tags: int = 9) -> list[str]:
    """Generate relevant YouTube Shorts hashtags (without leading '#') for a map topic."""
    region_tag = "europe" if map_type in ("default", "flag") else "world"
    tags: list[str] = ["shorts", "maps", "geography", region_tag]
    seen: set[str] = set(tags)

    def _add(raw: str) -> None:
        clean = re.sub(r"[^a-z0-9]", "", raw.lower())
        if len(clean) >= 3 and clean not in seen and clean not in _TAG_STOPWORDS:
            seen.add(clean)
            tags.append(clean)

    topic_low = str(topic or "").lower()
    for pattern, extras in _THEME_HASHTAGS:
        if re.search(pattern, topic_low):
            for ex in extras:
                _add(ex)

    for word in re.findall(r"[a-zA-Z0-9']+", topic_low):
        _add(word.replace("'", ""))
        if len(tags) >= max_tags:
            break

    _add("mapping")
    _add("facts")
    return tags[:max_tags]


def render_video_description(
    topic: str,
    map_type: str = "default",
    template: str = "",
) -> tuple[str, list[str]]:
    """Render a YouTube description from the user's template and auto-generated topic hashtags."""
    clean_topic = str(topic or "").strip()
    tags = generate_topic_hashtags(clean_topic, map_type=map_type)
    hashtags_str = " ".join(f"#{t}" for t in tags)

    tpl = str(template or "").strip()
    if not tpl:
        return f"{clean_topic}\n\n{hashtags_str}", tags

    rendered = tpl.replace("{topic}", clean_topic).replace("{hashtags}", hashtags_str)
    if "{topic}" not in tpl and clean_topic:
        rendered = f"{clean_topic}\n\n{rendered}"
    if "{hashtags}" not in tpl and hashtags_str:
        rendered = f"{rendered}\n\n{hashtags_str}"
    return rendered[:5000], tags

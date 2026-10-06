"""Real-world weather forecast client (weatherapi.com) for the HIL driver briefing.

WHAT THIS IS NOT
================
This module has nothing to do with `gsm_sim.environment.EnvironmentContext`, which models
rain/temperature/events *synthetically*, CRN-safe and seeded, and which feeds demand, speed
and battery inside the simulated world.

Those two must never meet:

  * `EnvironmentContext` is a MEASUREMENT INPUT. Its weather series is generated up-front from
    the seed so arms A/B/C stay comparable. Feeding live API data into it would destroy
    determinism and drift the measurement fingerprint (CLAUDE.md 1c, frozen blue zone).
  * This module is a PRESENTATION INPUT. It describes the real Hanoi sky so a human sitting in
    a role-play session can plan a shift the way they would in real life.

Consequence the UI must state plainly: rain shown here does NOT depress demand in the
simulation. Anything else would be lying to the person reading it.

BOUNDARIES (CLAUDE.md 5)
========================
* Every number handed onward comes verbatim from the provider response. Nothing is estimated,
  interpolated, or smoothed. There is no model here on purpose.
* Fail-closed: no key, timeout, HTTP error, or malformed payload returns ``None``. Callers must
  render nothing rather than substitute a guess. A missing forecast is a fine outcome; an
  invented one is not.
* `weather` is a SOFT topic (`gsm_core.lifecycle.advice_topics.SOFT_TOPICS`). Nothing derived
  from this module may ever be scored for adherence.

CACHING
=======
`.env.example` states local caching is mandatory: the free tier is small and a HIL session polls
its snapshot endpoint continuously. Responses are cached on disk under a gitignored path, keyed
by location and TTL-bounded, so a whole session costs one upstream call.

No third-party HTTP dependency: this uses `urllib.request` exactly as
`ui/backend/app/routers/routing.py` does for OSRM/GraphHopper.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]

# Gitignored (see .gitignore: data/ui-telemetry/). Cache is disposable by definition.
CACHE_DIR = ROOT / "data" / "ui-telemetry" / "weather-cache"

DEFAULT_LOCATION = "Hanoi"
DEFAULT_TTL_S = 1800.0          # 30 min: finer than the provider's own hourly granularity
HTTP_TIMEOUT_S = 5.0            # same budget routing.py allows its upstreams

# A driver plans around "will I be rained on", not around millimetres. 50% is the provider's own
# coin-flip boundary and keeps the highlighted hours few enough to stay readable.
RAIN_CHANCE_THRESHOLD = 50

_HTTP_HEADERS = {"User-Agent": "gsm-driver-agent/1.0 (+weather-brief)"}


@dataclass(frozen=True)
class HourlyForecast:
    """One hour of the provider's forecast. Every field is copied, never computed."""

    hour: int                   # 0..23, local time
    time_local: str             # provider's "YYYY-MM-DD HH:MM"
    temp_c: float
    feelslike_c: float
    chance_of_rain: int         # percent, 0..100
    will_it_rain: bool
    precip_mm: float
    condition: str              # provider's own wording, e.g. "Patchy rain nearby"
    # Stable numeric code. The wording above is free text and can change or be localised,
    # so anything that MAPS (icons, severity) must key off this, never off `condition`.
    condition_code: int = 0

    @property
    def is_wet(self) -> bool:
        """Wet enough to be worth flagging to a driver on a motorbike."""
        return self.chance_of_rain >= RAIN_CHANCE_THRESHOLD


@dataclass(frozen=True)
class DayForecast:
    """A whole calendar day, 24 hours, plus the provenance needed to cite it."""

    location: str               # provider's resolved name, not the query string
    region: str
    country: str
    localtime: str              # local clock at the location when the provider answered
    date: str                   # "YYYY-MM-DD"
    hours: tuple[HourlyForecast, ...]
    max_temp_c: float
    min_temp_c: float
    daily_chance_of_rain: int
    sunrise: str
    sunset: str
    condition: str              # the day's overall condition text
    fetched_at: str             # UTC ISO-8601, when WE called the provider
    source: str = "weatherapi.com"
    from_cache: bool = False

    # -- derived views. These select and count; they never invent a value. --

    @property
    def wet_hours(self) -> tuple[HourlyForecast, ...]:
        return tuple(h for h in self.hours if h.is_wet)

    def wet_hours_after(self, hour: int) -> tuple[HourlyForecast, ...]:
        """Wet hours still ahead of `hour`.

        A forecast for an hour that has already passed is not a warning, it is noise -- the
        same mistake the shift-opening bulletin made when it named demand windows that had
        already gone by (hil_loi_khuyen.py, item 13).
        """
        return tuple(h for h in self.wet_hours if h.hour > hour)

    def next_wet_hour(self, after_hour: int) -> HourlyForecast | None:
        upcoming = self.wet_hours_after(after_hour)
        return upcoming[0] if upcoming else None

    @property
    def has_rain(self) -> bool:
        return bool(self.wet_hours)


# ---------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------

def _hour_of(time_local: str) -> int:
    """"2026-08-30 14:00" -> 14. Raises on anything else; the caller treats that as malformed."""
    return int(time_local.split(" ")[1].split(":")[0])


def parse_forecast(payload: dict[str, Any]) -> DayForecast | None:
    """Turn a `forecast.json` body into a `DayForecast`, or `None` if it is not usable.

    Deliberately strict. A partially-understood payload is exactly the case where a plausible
    but wrong number reaches a driver, so anything unexpected fails closed rather than being
    patched up with defaults.
    """
    try:
        loc = payload["location"]
        day_block = payload["forecast"]["forecastday"][0]
        day = day_block["day"]
        astro = day_block.get("astro") or {}
        raw_hours = day_block["hour"]
        if len(raw_hours) != 24:
            _LOG.warning("weather: expected 24 hourly entries, got %d", len(raw_hours))
            return None

        hours = tuple(
            HourlyForecast(
                hour=_hour_of(h["time"]),
                time_local=h["time"],
                temp_c=float(h["temp_c"]),
                feelslike_c=float(h.get("feelslike_c", h["temp_c"])),
                chance_of_rain=int(h.get("chance_of_rain", 0)),
                will_it_rain=bool(int(h.get("will_it_rain", 0))),
                precip_mm=float(h.get("precip_mm", 0.0)),
                condition=str((h.get("condition") or {}).get("text", "")).strip(),
                condition_code=int((h.get("condition") or {}).get("code", 0) or 0),
            )
            for h in raw_hours
        )
        if [h.hour for h in hours] != list(range(24)):
            _LOG.warning("weather: hourly entries are not 0..23 in order")
            return None

        return DayForecast(
            location=str(loc.get("name", "")),
            region=str(loc.get("region", "")),
            country=str(loc.get("country", "")),
            localtime=str(loc.get("localtime", "")),
            date=str(day_block["date"]),
            hours=hours,
            max_temp_c=float(day["maxtemp_c"]),
            min_temp_c=float(day["mintemp_c"]),
            daily_chance_of_rain=int(day.get("daily_chance_of_rain", 0)),
            sunrise=str(astro.get("sunrise", "")),
            sunset=str(astro.get("sunset", "")),
            condition=str((day.get("condition") or {}).get("text", "")).strip(),
            fetched_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        _LOG.warning("weather: unusable payload (%s: %s)", type(exc).__name__, exc)
        return None


# ---------------------------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------------------------

def _cache_path(location: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in location.lower())
    return CACHE_DIR / f"{safe}.json"


def _read_cache(location: str, ttl_s: float, now: float) -> dict[str, Any] | None:
    """Cached payload, or None when it is missing or older than ``ttl_s``.

    The comparison is ``>=`` rather than ``>`` so that ``ttl_s=0`` means what it reads as:
    never serve from cache. Under ``>``, an entry whose age rounds to exactly zero counted as
    fresh, so "everything is stale" depended on the sub-millisecond gap between writing the
    file and reading the clock — a caller passing 0 got a cache hit or a network call
    depending on timing. For every other TTL the boundary moves by one instant and nothing
    observable changes.
    """
    p = _cache_path(location)
    try:
        if not p.exists() or (now - p.stat().st_mtime) >= ttl_s:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _LOG.debug("weather: cache unreadable (%s)", exc)
        return None


def _write_cache(location: str, payload: dict[str, Any]) -> None:
    """Best-effort. A cache that cannot be written must never break the request path."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(location).write_text(json.dumps(payload), encoding="utf-8")
    except (OSError, TypeError) as exc:
        _LOG.debug("weather: cache not written (%s)", exc)


# ---------------------------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------------------------

def _http_get_json(url: str, timeout: float) -> dict[str, Any] | None:
    try:
        req = urllib.request.Request(url, headers=_HTTP_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            ValueError, OSError) as exc:
        # Includes 401 from a missing/rotated key and 400 from a bad location. All the same
        # to the caller: there is no forecast, so nothing gets displayed.
        _LOG.warning("weather: fetch failed (%s: %s)", type(exc).__name__, exc)
        return None


def fetch_day(location: str = DEFAULT_LOCATION, *,
              ttl_s: float = DEFAULT_TTL_S,
              use_cache: bool = True,
              _now: float | None = None) -> DayForecast | None:
    """Today's 24-hour forecast for `location`, or `None` if it cannot be obtained.

    `None` is a normal, expected result -- no key configured, provider down, network absent.
    Callers must render nothing in that case. Never substitute a placeholder forecast: a wrong
    forecast is worse than no forecast, because the driver acts on it.
    """
    import time

    now = time.time() if _now is None else _now

    if use_cache:
        cached = _read_cache(location, ttl_s, now)
        if cached is not None:
            parsed = parse_forecast(cached)
            if parsed is not None:
                return DayForecast(**{**parsed.__dict__, "from_cache": True})

    # Imported here so the module stays importable without a configured environment, which is
    # what lets the test-suite exercise parsing with no .env present.
    from gsm_core.advisor.llm_client import load_env

    load_env()
    api_key = os.environ.get("WEATHER_API_KEY", "").strip()
    base = os.environ.get("WEATHER_BASE_URL", "").strip()
    if not api_key or not base:
        _LOG.info("weather: WEATHER_API_KEY or WEATHER_BASE_URL not set -- no forecast")
        return None

    query = urllib.parse.urlencode(
        {"key": api_key, "q": location, "days": 1, "aqi": "no", "alerts": "no"})
    payload = _http_get_json(f"{base.rstrip('/')}/forecast.json?{query}", HTTP_TIMEOUT_S)
    if payload is None:
        return None

    parsed = parse_forecast(payload)
    if parsed is None:
        return None
    if use_cache:
        _write_cache(location, payload)
    return parsed

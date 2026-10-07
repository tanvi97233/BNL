from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def last_working_day(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day - timedelta(days=2)
    return day


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def resolve_date_range(start_value: str | None, end_value: str | None, *, today: date | None = None) -> tuple[date, date]:
    """CLI values win over env values; a missing end defaults to last working day."""
    today = today or date.today()
    end = parse_date(end_value) if end_value else last_working_day(today)
    start = parse_date(start_value) if start_value else end - timedelta(days=14)
    if start > end:
        raise ValueError("start date must not be after end date")
    return start, end


@dataclass(frozen=True)
class Settings:
    xai_api_key: str
    xai_base_url: str
    xai_model: str
    output_dir: Path = ROOT / "output"
    cache_dir: Path = ROOT / ".cache"
    timeout_seconds: int = 25
    browser_timeout_ms: int = 30_000
    google_delay_ms: int = 1200
    google_home_wait_ms: int = 2500
    browser_headless: bool = False
    browser_profile_dir: Path = ROOT / ".cache" / "google-profile"
    browser_executable_path: str = ""
    browser_channel: str = "auto"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            xai_api_key=os.getenv("XAI_API_KEY", ""),
            xai_base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").rstrip("/"),
            xai_model=os.getenv("XAI_MODEL", "grok-4"),
            output_dir=Path(os.getenv("OUTPUT_DIR", str(ROOT / "output"))),
            cache_dir=Path(os.getenv("CACHE_DIR", str(ROOT / ".cache"))),
            timeout_seconds=int(os.getenv("HTTP_TIMEOUT_SECONDS", "25")),
            browser_timeout_ms=int(os.getenv("BROWSER_TIMEOUT_MS", "30000")),
            google_delay_ms=int(os.getenv("GOOGLE_DELAY_MS", "1200")),
            google_home_wait_ms=int(os.getenv("GOOGLE_HOME_WAIT_MS", "2500")),
            browser_headless=os.getenv("BROWSER_HEADLESS", "false").lower() not in {"0", "false", "no"},
            browser_profile_dir=Path(os.getenv("BROWSER_PROFILE_DIR", str(ROOT / ".cache" / "google-profile"))),
            browser_executable_path=os.getenv("BROWSER_EXECUTABLE_PATH", ""),
            browser_channel=os.getenv("BROWSER_CHANNEL", "auto").lower(),
        )

    def with_overrides(self, **values: object) -> "Settings":
        return replace(self, **values)

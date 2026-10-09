"""Plan usage limits (remaining quota and reset times) in one shape for the GUI.

- Codex: `account/rateLimits/read` from app-server (structured).
- Claude Code: the CLI's local `/usage` command (no model call, no tokens),
  plus `RateLimitEvent`s the CLI sends during runs.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime

CLAUDE_WINDOW_LABELS = {
    "five_hour": "5時間枠",
    "seven_day": "1週間（全モデル）",
    "seven_day_opus": "1週間（Opus）",
    "seven_day_sonnet": "1週間（Sonnet）",
    "overage": "追加利用",
}


@dataclass
class UsageWindow:
    key: str
    label: str
    used_percent: float
    resets_at: float | None = None  # unix seconds
    reset_text: str = ""  # as reported, when it could not be parsed


@dataclass
class UsageInfo:
    provider: str
    available: bool = True
    windows: list[UsageWindow] = field(default_factory=list)
    note: str = ""  # e.g. "APIキー利用のため利用枠はありません"
    extra: str = ""  # e.g. available reset credits
    fetched_at: float = field(default_factory=time.time)

    def public(self) -> dict:
        return asdict(self)


# Codex ----------------------------------------------------------------------------------


def _window_label(minutes: int | None, fallback: str) -> str:
    if not minutes:
        return fallback
    if minutes % 10080 == 0:
        return "1週間" if minutes == 10080 else f"{minutes // 10080}週間"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}日"
    if minutes % 60 == 0:
        return f"{minutes // 60}時間枠"
    return f"{minutes}分枠"


def codex_usage(result: dict) -> UsageInfo:
    info = UsageInfo("codex")
    limits = (result or {}).get("rateLimits") or {}
    for key, fallback in (("primary", "短期枠"), ("secondary", "長期枠")):
        w = limits.get(key)
        if w and w.get("usedPercent") is not None:
            info.windows.append(UsageWindow(key, _window_label(w.get("windowDurationMins"), fallback),
                                            float(w["usedPercent"]), w.get("resetsAt")))
    credits = (result or {}).get("rateLimitResetCredits") or {}
    if credits.get("availableCount"):
        info.extra = f"利用枠リセット券 {credits['availableCount']} 枚"
    if limits.get("rateLimitReachedType"):
        info.note = "利用上限に達しています"
    if not info.windows and not info.note:
        info.note = "利用枠の情報がありません（APIキー利用など）"
    return info


# Claude Code ----------------------------------------------------------------------------

_LINE = re.compile(r"^\s*(?P<label>[^:\n]+?):\s*(?P<pct>\d+(?:\.\d+)?)%\s*used(?:\s*[·•-]\s*resets\s+(?P<reset>.+?))?\s*$",
                   re.I | re.M)
_RESET = re.compile(r"^(?P<mon>[A-Za-z]{3})[a-z]*\s+(?P<day>\d{1,2})(?:,?\s+(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ap>am|pm))?",
                    re.I)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_reset(text: str, now: datetime | None = None) -> float | None:
    """'Oct 9, 3:40pm (Asia/Tokyo)' -> unix time. The CLI prints in local time."""
    m = _RESET.match(text.strip())
    if not m or m["mon"].lower() not in _MONTHS:
        return None
    now = now or datetime.now()
    hour = int(m["h"] or 0)
    if m["ap"]:
        hour = hour % 12 + (12 if m["ap"].lower() == "pm" else 0)
    dt = datetime(now.year, _MONTHS[m["mon"].lower()], int(m["day"]), hour, int(m["m"] or 0))
    if (now - dt).days > 180:  # e.g. "Jan 2" seen in late December
        dt = dt.replace(year=now.year + 1)
    return dt.timestamp()


def _claude_key(label: str) -> tuple[str, str]:
    low = label.lower()
    if "session" in low:
        return "five_hour", CLAUDE_WINDOW_LABELS["five_hour"]
    if "week" in low:
        for model in ("opus", "sonnet", "fable", "haiku"):
            if model in low:
                return f"seven_day_{model}", f"1週間（{model.capitalize()}）"
        return "seven_day", CLAUDE_WINDOW_LABELS["seven_day"]
    return re.sub(r"\W+", "_", low).strip("_"), label


def claude_usage_from_text(text: str, now: datetime | None = None) -> UsageInfo:
    info = UsageInfo("claude")
    for m in _LINE.finditer(text or ""):
        key, label = _claude_key(m["label"])
        reset = m["reset"] or ""
        info.windows.append(UsageWindow(key, label, float(m["pct"]), parse_reset(reset, now) if reset else None,
                                        reset if reset and parse_reset(reset, now) is None else ""))
    if not info.windows:
        info.note = ("利用枠の情報がありません（APIキー利用の場合は利用枠ではなく従量課金です）"
                     if "api" in (text or "").lower() else "利用枠の情報を取得できませんでした")
    return info


def apply_claude_event(info: UsageInfo | None, rate: dict) -> UsageInfo:
    """Update the cached Claude usage from a RateLimitEvent payload."""
    info = info or UsageInfo("claude")
    key = rate.get("rate_limit_type") or "five_hour"
    used = rate.get("utilization")
    if used is None:
        return info
    window = next((w for w in info.windows if w.key == key), None)
    if window is None:
        window = UsageWindow(key, CLAUDE_WINDOW_LABELS.get(key, key), 0)
        info.windows.append(window)
    window.used_percent = round(float(used) * 100, 1)
    if rate.get("resets_at"):
        window.resets_at = float(rate["resets_at"])
    if rate.get("status") == "rejected":
        info.note = "利用上限に達しています"
    info.fetched_at = time.time()
    return info

from datetime import datetime

from roxy_ai.usage import apply_claude_event, claude_usage_from_text, codex_usage, parse_reset

CLAUDE_TEXT = """You are currently using your subscription to power your Claude Code usage

Current session: 87% used · resets Oct 9, 3:40pm (Asia/Tokyo)
Current week (all models): 56% used · resets Oct 14, 5pm (Asia/Tokyo)
Current week (Sonnet only): 12% used · resets Oct 14, 5pm (Asia/Tokyo)
"""


def test_claude_usage_text():
    info = claude_usage_from_text(CLAUDE_TEXT, datetime(2026, 10, 9, 13, 50))
    got = [(w.key, w.used_percent, datetime.fromtimestamp(w.resets_at)) for w in info.windows]
    assert got == [
        ("five_hour", 87.0, datetime(2026, 10, 9, 15, 40)),
        ("seven_day", 56.0, datetime(2026, 10, 14, 17, 0)),
        ("seven_day_sonnet", 12.0, datetime(2026, 10, 14, 17, 0)),
    ]
    assert claude_usage_from_text("Usage is billed to your API key").windows == []


def test_reset_rolls_into_next_year():
    ts = parse_reset("Jan 2, 9am (Asia/Tokyo)", datetime(2026, 12, 30))
    assert datetime.fromtimestamp(ts) == datetime(2027, 1, 2, 9, 0)
    assert parse_reset("soon") is None


def test_claude_event_updates_window():
    info = claude_usage_from_text(CLAUDE_TEXT, datetime(2026, 10, 9, 13, 50))
    info = apply_claude_event(info, {"rate_limit_type": "five_hour", "utilization": 0.93, "resets_at": 1791530000, "status": "allowed_warning"})
    w = info.windows[0]
    assert w.used_percent == 93.0 and w.resets_at == 1791530000


def test_codex_usage():
    info = codex_usage({
        "rateLimits": {"primary": {"usedPercent": 17, "windowDurationMins": 300, "resetsAt": 1791530379},
                       "secondary": {"usedPercent": 30, "windowDurationMins": 10080, "resetsAt": 1791948814}},
        "rateLimitResetCredits": {"availableCount": 2},
    })
    assert [(w.label, w.used_percent, w.resets_at) for w in info.windows] == [
        ("5時間枠", 17.0, 1791530379), ("1週間", 30.0, 1791948814)]
    assert "2" in info.extra

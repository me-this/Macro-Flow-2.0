"""
config.py - central configuration for the market-order dual-terminal algo.
Same hardcoded schedule, timezone handling, dashboard and tunnel settings as
the stop-order algo. The trading-parameters section is different: no stop
distance or order count (single market order per terminal), TP added.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

# ============================================================
# TRADING PARAMETERS
# ============================================================
SYMBOL = "XAUUSD"
LOT_SIZE = 3.50

# How many seconds BEFORE release to send the market order. Calm-market
# round-trip latency measured at ~112ms (see market_order_latency_test.py
# results) - this is NOT a news-time number. 2s gives ~18x that as buffer.
# Verify against your first live run and adjust.
PRE_RELEASE_LEAD_SECONDS = 2

INITIAL_SL_POINTS = 200      # starting point: matches latency test values
TAKE_PROFIT_POINTS = 2000     # starting point: matches latency test values - tune deliberately
BREAKEVEN_TRIGGER_POINTS = 150
BREAKEVEN_LOCK_POINTS = 100
EXIT_SECONDS_AFTER_RELEASE = 60   # hard close, same rule as before

POLL_INTERVAL_SECONDS = 0.1
MAGIC_NUMBER = 990002   # different from the stop-order algo's magic number, kept distinct deliberately

# ============================================================
# DASHBOARD / TUNNEL / SCHEDULER SETTINGS (unchanged from the stop-order algo)
# ============================================================
DASHBOARD_API_PORT = 8787
EXECUTION_LOG_PATH = "execution_log.jsonl"
COUNTDOWN_LOG_START_SECONDS = 30 * 60
COUNTDOWN_LOG_INTERVAL_SECONDS = 60
SCHEDULER_IDLE_POLL_SECONDS = 60
QUIET_WINDOW_BEFORE_SECONDS = 30
QUIET_WINDOW_AFTER_SECONDS = 90

# ============================================================
# HARDCODED MACRO SCHEDULE - identical to the stop-order algo
# ============================================================
MACRO_SCHEDULE_2026 = [
    {"event": "Non-Farm Employment Change", "datetime_et": datetime(2026, 10, 2, 8, 30, tzinfo=EASTERN)},
    {"event": "Non-Farm Employment Change", "datetime_et": datetime(2026, 11, 6, 8, 30, tzinfo=EASTERN)},
    {"event": "Non-Farm Employment Change", "datetime_et": datetime(2026, 12, 4, 8, 30, tzinfo=EASTERN)},

    {"event": "CPI",                        "datetime_et": datetime(2026, 9, 11, 8, 30, tzinfo=EASTERN)},
    {"event": "CPI",                        "datetime_et": datetime(2026, 10, 14, 8, 30, tzinfo=EASTERN)},
    {"event": "CPI",                        "datetime_et": datetime(2026, 11, 10, 8, 30, tzinfo=EASTERN)},
    {"event": "CPI",                        "datetime_et": datetime(2026, 12, 10, 8, 30, tzinfo=EASTERN)},

    {"event": "PPI",                        "datetime_et": datetime(2026, 9, 10, 8, 30, tzinfo=EASTERN)},
    {"event": "PPI",                        "datetime_et": datetime(2026, 10, 15, 8, 30, tzinfo=EASTERN)},
    {"event": "PPI",                        "datetime_et": datetime(2026, 11, 13, 8, 30, tzinfo=EASTERN)},
    {"event": "PPI",                        "datetime_et": datetime(2026, 12, 15, 8, 30, tzinfo=EASTERN)},

    {"event": "FOMC Statement",             "datetime_et": datetime(2026, 9, 16, 14, 0, tzinfo=EASTERN)},
    {"event": "FOMC Statement",             "datetime_et": datetime(2026, 10, 28, 14, 0, tzinfo=EASTERN)},
    {"event": "FOMC Statement",             "datetime_et": datetime(2026, 12, 9, 14, 0, tzinfo=EASTERN)},
]

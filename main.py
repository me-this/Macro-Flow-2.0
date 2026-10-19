"""
main.py - orchestrator for the market-order dual-terminal algo. Run on the
GitHub Actions Windows RDP session.

Requires EXACTLY 2 MT5 terminals detected at launch time for any event -
if only one (or more than two) is found, that event is skipped entirely
rather than executing one-sided exposure.
"""
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import config
import api_server
import tunnel
from terminal_detector import find_mt5_terminals


def get_events_in_window(hours_ahead=6):
    now_utc = datetime.now(timezone.utc)
    window_end = now_utc + timedelta(hours=hours_ahead)
    upcoming = []
    for evt in config.MACRO_SCHEDULE_2026:
        release_utc = evt["datetime_et"].astimezone(timezone.utc)
        if now_utc < release_utc <= window_end:
            upcoming.append({"event": evt["event"], "release_time_utc": release_utc})
    upcoming.sort(key=lambda e: e["release_time_utc"])
    return upcoming


def run_event_on_both_terminals(event_name, release_time_utc):
    terminals = find_mt5_terminals()

    if len(terminals) != 2:
        print(f"[main] {event_name}: expected exactly 2 MT5 terminals, found {len(terminals)} "
              f"({terminals}). SKIPPING this event - no orders will be placed.")
        return

    buy_terminal, sell_terminal = terminals[0], terminals[1]
    print(f"[main] {event_name} at {release_time_utc.isoformat()} -> "
          f"BUY on {buy_terminal} | SELL on {sell_terminal}")

    processes = [
        subprocess.Popen([
            sys.executable, "market_executor.py",
            "--terminal-path", buy_terminal,
            "--event-name", event_name,
            "--release-time-utc", release_time_utc.isoformat(),
            "--side", "buy",
        ]),
        subprocess.Popen([
            sys.executable, "market_executor.py",
            "--terminal-path", sell_terminal,
            "--event-name", event_name,
            "--release-time-utc", release_time_utc.isoformat(),
            "--side", "sell",
        ]),
    ]

    for proc in processes:
        proc.wait()

    print(f"[main] Both terminals finished handling {event_name}.")


def countdown_and_launch(event_name, release_time_utc):
    while True:
        now = datetime.now(timezone.utc)
        seconds_until = (release_time_utc - now).total_seconds()

        if seconds_until <= 20:
            break

        if seconds_until <= config.COUNTDOWN_LOG_START_SECONDS:
            mins, secs = divmod(int(seconds_until), 60)
            print(f"[main] Countdown to {event_name}: T-{mins:02d}:{secs:02d} "
                  f"(release {release_time_utc.isoformat()})")
            time.sleep(min(config.COUNTDOWN_LOG_INTERVAL_SECONDS, seconds_until - 20))
        else:
            wait = seconds_until - config.COUNTDOWN_LOG_START_SECONDS
            print(f"[main] {event_name} is {wait/60:.1f} more minutes away before countdown logging starts.")
            time.sleep(min(wait, 300))

    run_event_on_both_terminals(event_name, release_time_utc)


def main():
    threading.Thread(target=api_server.run_api_server, daemon=True).start()
    print(f"[main] Dashboard API server running on port {config.DASHBOARD_API_PORT}.")

    time.sleep(2)
    bore_process, dashboard_url = tunnel.start_tunnel(config.DASHBOARD_API_PORT)
    print(f"[main] ============================================")
    print(f"[main] DASHBOARD READY: {dashboard_url}/")
    print(f"[main] ============================================")

    while True:
        events = get_events_in_window(hours_ahead=6)
        if not events:
            print("[main] No macro events in the next 6 hours. Dashboard stays up; rechecking shortly.")
            time.sleep(config.SCHEDULER_IDLE_POLL_SECONDS)
            continue

        next_event = events[0]
        countdown_and_launch(next_event["event"], next_event["release_time_utc"])


if __name__ == "__main__":
    main()

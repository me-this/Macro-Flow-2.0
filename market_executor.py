r"""
market_executor.py - opens ONE market order (buy or sell, assigned by
main.py) on a single terminal, seconds before a scheduled macro release.
No stop orders, no fill-waiting - a market order either executes
immediately (retcode DONE) or fails outright. SL and TP are attached at
entry. Breakeven and the 60-second hard exit work the same as the
stop-order algo.

Standalone usage:
    python market_executor.py --terminal-path "C:\path\to\terminal64.exe" --event-name "CPI" --release-time-utc "2026-10-14T12:30:00+00:00" --side buy
"""
import argparse
import json
import time
from datetime import datetime, timedelta, timezone

import MetaTrader5 as mt5
import config


def log(tag, msg):
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] [{tag}] {msg}")


def resolve_symbol(base_symbol, event_name):
    all_symbols = mt5.symbols_get()
    if not all_symbols:
        raise RuntimeError("No symbols returned by terminal - check MT5 connection.")

    candidates = [s.name for s in all_symbols if s.name.upper().startswith(base_symbol.upper())]
    if not candidates:
        raise RuntimeError(f"No symbol starting with '{base_symbol}' found on this terminal.")

    exact = [c for c in candidates if c.upper() == base_symbol.upper()]
    resolved = exact[0] if exact else sorted(candidates, key=len)[0]

    log(event_name, f"SYMBOL RESOLVED: '{base_symbol}' -> '{resolved}' (candidates seen: {candidates})")
    return resolved


def connect(terminal_path, event_name):
    if not mt5.initialize(path=terminal_path):
        raise RuntimeError(f"MT5 initialize() failed for {terminal_path}: {mt5.last_error()}")

    symbol = resolve_symbol(config.SYMBOL, event_name)

    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol {symbol} resolved but symbol_info() returned None.")
    if not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)

    acct = mt5.account_info()
    log(event_name, f"CONNECTED. Account={acct.login if acct else 'unknown'} "
                     f"Broker={acct.company if acct else 'unknown'} Symbol={symbol} Point={info.point}")
    return info, symbol


def check_broker_time_offset(symbol, event_name):
    tick = mt5.symbol_info_tick(symbol)
    broker_time = datetime.fromtimestamp(tick.time, tz=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    offset_hours = (broker_time - now_utc).total_seconds() / 3600
    log(event_name, f"DIAG: broker server time offset from true UTC: {offset_hours:+.2f}h")
    if abs(offset_hours) > 6:
        log(event_name, "DIAG WARNING: broker offset looks unusual - double check terminal clock.")


def get_filling_mode(symbol_info):
    SYMBOL_FILLING_FOK = 1
    SYMBOL_FILLING_IOC = 2
    mode = symbol_info.filling_mode
    if mode & SYMBOL_FILLING_IOC:
        return mt5.ORDER_FILLING_IOC
    if mode & SYMBOL_FILLING_FOK:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def wait_until(target_utc):
    while True:
        now = datetime.now(timezone.utc)
        remaining = (target_utc - now).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.05))


def append_execution_log(event_name, release_time_utc, side, entry_price, exit_price, outcome, slippage_points=None):
    record = {
        "event": event_name,
        "release_time_utc": release_time_utc.isoformat(),
        "closed_at_utc": datetime.now(timezone.utc).isoformat(),
        "side": side,
        "entry": entry_price,
        "exit": exit_price,
        "outcome": outcome,
        "slippage_points": slippage_points,
    }
    try:
        with open(config.EXECUTION_LOG_PATH, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        log(event_name, f"Failed to write execution log: {e}")


def open_market_order(symbol, symbol_info, filling_mode, side, event_name, release_time_utc):
    point = symbol_info.point
    tick = mt5.symbol_info_tick(symbol)

    if side == "buy":
        order_type = mt5.ORDER_TYPE_BUY
        requested_price = tick.ask
        sl = round(requested_price - config.INITIAL_SL_POINTS * point, symbol_info.digits)
        tp = round(requested_price + config.TAKE_PROFIT_POINTS * point, symbol_info.digits)
    else:
        order_type = mt5.ORDER_TYPE_SELL
        requested_price = tick.bid
        sl = round(requested_price + config.INITIAL_SL_POINTS * point, symbol_info.digits)
        tp = round(requested_price - config.TAKE_PROFIT_POINTS * point, symbol_info.digits)

    log(event_name, f"[{side.upper()}] REQUESTING market order. requested_price={requested_price} sl={sl} tp={tp}")

    req = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": config.LOT_SIZE,
        "type": order_type, "price": requested_price, "sl": sl, "tp": tp,
        "type_filling": filling_mode, "magic": config.MAGIC_NUMBER,
        "comment": f"market_{side}_{event_name[:10]}",
    }

    t_before = time.perf_counter()
    result = mt5.order_send(req)
    t_before_ms = (time.perf_counter() - t_before) * 1000

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log(event_name, f"[{side.upper()}] ORDER FAILED retcode={result.retcode} comment={result.comment} "
                         f"(round_trip={t_before_ms:.1f}ms)")
        append_execution_log(event_name, release_time_utc, side, None, None, f"order_failed_{result.retcode}")
        return None

    time.sleep(0.1)
    positions = mt5.positions_get(ticket=result.order)
    actual_price = positions[0].price_open if positions else result.price
    slippage_points = (actual_price - requested_price) / point if side == "buy" else (requested_price - actual_price) / point

    log(event_name, f"[{side.upper()}] ORDER DONE ticket={result.order} requested={requested_price} "
                     f"actual_fill={actual_price} slippage={slippage_points:+.1f}pts "
                     f"round_trip={t_before_ms:.1f}ms")

    return result.order


def get_our_positions(symbol):
    positions = mt5.positions_get(symbol=symbol)
    if not positions:
        return []
    return [p for p in positions if p.magic == config.MAGIC_NUMBER]


def detect_external_closes(symbol, symbol_info, known_positions, event_name):
    point = symbol_info.point
    current_positions = get_our_positions(symbol)
    current_tickets = {p.ticket for p in current_positions}

    for ticket, info in list(known_positions.items()):
        if ticket in current_tickets:
            continue
        deals = mt5.history_deals_get(position=ticket)
        close_deal = deals[-1] if deals else None
        if close_deal:
            exit_price = close_deal.price
            pnl_points = (exit_price - info["entry"]) if info["side"] == "buy" else (info["entry"] - exit_price)
            pnl_points /= point
            log(event_name, f"POSITION CLOSED EXTERNALLY (SL or TP hit) ticket={ticket} "
                             f"side={info['side']} entry={info['entry']} exit={exit_price} result={pnl_points:+.1f}pts")
            append_execution_log(event_name, info["release_time_utc"], info["side"], info["entry"], exit_price, "sl_or_tp_hit")
        else:
            log(event_name, f"POSITION CLOSED EXTERNALLY ticket={ticket} (could not fetch close deal from history)")
        del known_positions[ticket]

    for pos in current_positions:
        if pos.ticket not in known_positions:
            known_positions[pos.ticket] = {
                "side": "buy" if pos.type == mt5.ORDER_TYPE_BUY else "sell",
                "entry": pos.price_open,
                "release_time_utc": None,
            }


def manage_breakeven(symbol, symbol_info, breakeven_done, event_name):
    point = symbol_info.point
    tick = mt5.symbol_info_tick(symbol)
    for pos in get_our_positions(symbol):
        if pos.ticket in breakeven_done:
            continue
        if pos.type == mt5.ORDER_TYPE_BUY:
            profit_points = (tick.bid - pos.price_open) / point
            new_sl = round(pos.price_open + config.BREAKEVEN_LOCK_POINTS * point, symbol_info.digits)
        else:
            profit_points = (pos.price_open - tick.ask) / point
            new_sl = round(pos.price_open - config.BREAKEVEN_LOCK_POINTS * point, symbol_info.digits)

        if profit_points >= config.BREAKEVEN_TRIGGER_POINTS:
            result = mt5.order_send({
                "action": mt5.TRADE_ACTION_SLTP, "position": pos.ticket, "symbol": symbol,
                "sl": new_sl, "tp": pos.tp,
            })
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                log(event_name, f"BREAKEVEN SET position={pos.ticket} new_sl={new_sl} "
                                 f"(locked {config.BREAKEVEN_LOCK_POINTS}pts)")
                breakeven_done.add(pos.ticket)
            else:
                log(event_name, f"BREAKEVEN MODIFY FAILED position={pos.ticket} retcode={result.retcode}")


def close_all_positions(symbol, symbol_info, filling_mode, event_name, release_time_utc):
    positions = get_our_positions(symbol)
    if not positions:
        log(event_name, "HARD EXIT: no open position to close.")
        return

    tick = mt5.symbol_info_tick(symbol)
    for pos in positions:
        if pos.type == mt5.ORDER_TYPE_BUY:
            close_type, close_price, side = mt5.ORDER_TYPE_SELL, tick.bid, "buy"
        else:
            close_type, close_price, side = mt5.ORDER_TYPE_BUY, tick.ask, "sell"

        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": pos.volume,
            "type": close_type, "position": pos.ticket, "price": close_price,
            "type_filling": filling_mode, "magic": config.MAGIC_NUMBER, "comment": "hard_exit_60s",
        }
        result = mt5.order_send(req)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            pnl_points = (close_price - pos.price_open) if side == "buy" else (pos.price_open - close_price)
            pnl_points /= symbol_info.point
            log(event_name, f"HARD EXIT CLOSED position={pos.ticket} side={side} entry={pos.price_open} "
                             f"exit={close_price} result={pnl_points:+.1f}pts")
            append_execution_log(event_name, release_time_utc, side, pos.price_open, close_price, "closed_60s")
        else:
            log(event_name, f"HARD EXIT CLOSE FAILED position={pos.ticket} retcode={result.retcode} {result.comment}")
            append_execution_log(event_name, release_time_utc, side, pos.price_open, None, f"close_failed_{result.retcode}")


def reconciliation_sweep(symbol, event_name, release_time_utc):
    try:
        date_from = release_time_utc - timedelta(seconds=30)
        date_to = datetime.now(timezone.utc) + timedelta(seconds=5)
        deals = mt5.history_deals_get(date_from, date_to)
        if not deals:
            log(event_name, "RECONCILIATION: no deals found in history for this window.")
            return
        our_deals = [d for d in deals if d.magic == config.MAGIC_NUMBER and d.symbol == symbol]
        if not our_deals:
            log(event_name, "RECONCILIATION: no deals with our magic number found.")
            return
        log(event_name, f"RECONCILIATION: {len(our_deals)} deal(s) in MT5 history for this event:")
        for d in sorted(our_deals, key=lambda x: x.time):
            deal_time = datetime.fromtimestamp(d.time, tz=timezone.utc)
            entry_exit = "ENTRY" if d.entry == mt5.DEAL_ENTRY_IN else ("EXIT" if d.entry == mt5.DEAL_ENTRY_OUT else "OTHER")
            side = "buy" if d.type == mt5.DEAL_TYPE_BUY else "sell"
            log(event_name, f"RECONCILIATION [{entry_exit}] position={d.position_id} side={side} "
                             f"price={d.price} profit={d.profit} time={deal_time.isoformat()}")
    except Exception as e:
        log(event_name, f"RECONCILIATION FAILED: {e}")


def run(terminal_path, event_name, release_time_utc, side):
    subprocess_start = datetime.now(timezone.utc)
    log(event_name, f"SUBPROCESS STARTED at {subprocess_start.isoformat()} - assigned side: {side.upper()}")

    symbol_info, symbol = connect(terminal_path, event_name)
    setup_duration = (datetime.now(timezone.utc) - subprocess_start).total_seconds()
    log(event_name, f"SETUP (connect+resolve symbol) took {setup_duration:.2f}s")

    check_broker_time_offset(symbol, event_name)
    filling_mode = get_filling_mode(symbol_info)

    place_at_dt = release_time_utc - timedelta(seconds=config.PRE_RELEASE_LEAD_SECONDS)
    now_after_setup = datetime.now(timezone.utc)

    if now_after_setup >= place_at_dt:
        overrun = (now_after_setup - place_at_dt).total_seconds()
        log(event_name, f"CRITICAL: setup took too long - intended order time already passed by "
                         f"{overrun:.2f}s. ABORTING order for this terminal.")
        append_execution_log(event_name, release_time_utc, side, None, None, "aborted_late_setup")
        mt5.shutdown()
        return

    log(event_name, f"WAITING until {place_at_dt.isoformat()} to send {side.upper()} market order "
                     f"(release at {release_time_utc.isoformat()})")
    wait_until(place_at_dt)

    ticket = open_market_order(symbol, symbol_info, filling_mode, side, event_name, release_time_utc)
    if ticket is None:
        log(event_name, "No position opened. Exiting.")
        reconciliation_sweep(symbol, event_name, release_time_utc)
        mt5.shutdown()
        return

    breakeven_done = set()
    known_positions = {}
    hard_exit_done = False

    log(event_name, "MONITORING open position...")
    while True:
        now = datetime.now(timezone.utc)
        seconds_since_release = (now - release_time_utc).total_seconds()

        detect_external_closes(symbol, symbol_info, known_positions, event_name)
        for t in known_positions:
            if known_positions[t]["release_time_utc"] is None:
                known_positions[t]["release_time_utc"] = release_time_utc

        if get_our_positions(symbol):
            manage_breakeven(symbol, symbol_info, breakeven_done, event_name)
        elif known_positions == {} and hard_exit_done is False and ticket is not None and seconds_since_release > 2:
            # Position closed externally (SL/TP) before hard exit - nothing left to manage
            if not get_our_positions(symbol):
                break

        if not hard_exit_done and seconds_since_release >= config.EXIT_SECONDS_AFTER_RELEASE:
            log(event_name, f"HARD EXIT TRIGGERED at t+{seconds_since_release:.2f}s since release.")
            close_all_positions(symbol, symbol_info, filling_mode, event_name, release_time_utc)
            hard_exit_done = True
            break

        time.sleep(config.POLL_INTERVAL_SECONDS)

    reconciliation_sweep(symbol, event_name, release_time_utc)
    log(event_name, "DONE with this event on this terminal.")
    mt5.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--release-time-utc", required=True)
    parser.add_argument("--side", required=True, choices=["buy", "sell"])
    args = parser.parse_args()

    release_dt = datetime.fromisoformat(args.release_time_utc)
    run(args.terminal_path, args.event_name, release_dt, args.side)

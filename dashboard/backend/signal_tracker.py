"""Signal lifecycle tracker — records signal outcomes and performance metrics.

States: SCANNING → POTENTIAL → WAITING → CONFIRMED_LONG → CONFIRMED_SHORT → INVALIDATED → EXPIRED
Tracks: win/loss, R-multiple, PnL, fees, slippage, per-coin and per-timeframe statistics.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .risk_manager import validate_trade_plan
from .storage import atomic_write_json, safe_load_json

logger = logging.getLogger("signal_tracker")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = DATA_DIR / "signal_history.json"
if not HISTORY_FILE.exists() and (Path(__file__).resolve().parent.parent / "signal_history.json").exists():
    HISTORY_FILE = Path(__file__).resolve().parent.parent / "signal_history.json"
MAX_HISTORY = 5000


@dataclass
class TrackedSignal:
    """A signal with full lifecycle tracking."""

    signal_id: str
    symbol: str
    timeframe: str
    direction: str  # LONG, SHORT
    state: str  # CONFIRMED, INVALIDATED, EXPIRED, HIT_TP1, HIT_TP2, HIT_TP3, HIT_SL
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    invalidation_level: float
    setup_score: int
    created_at: int  # unix timestamp
    expires_at: int
    setup_state: str = "CONFIRMED"
    confirmed_at: int | None = None
    closed_at: int | None = None
    close_price: float | None = None
    close_reason: str = ""  # HIT_SL, HIT_TP1, HIT_TP2, HIT_TP3, EXPIRED, INVALIDATED, MANUAL
    realized_pnl_pct: float = 0.0
    r_multiple: float = 0.0
    fees_pct: float = 0.1  # default taker fee 0.1%
    slippage_pct: float = 0.05
    funding_cost_pct: float = 0.0
    reasons_for: list[str] = field(default_factory=list)
    reasons_against: list[str] = field(default_factory=list)
    htf_bias: str = ""
    strategy_type: str = ""  # MODEL_A, MODEL_B, MODEL_C
    filled: bool = True
    entry_range: list[float] = field(default_factory=list)
    risk_reward_ratio: float = 0.0
    price_precision: int | None = None
    mtf_reason: str = ""
    current_price: float | None = None
    current_price_timestamp: int | None = None
    market_data_fresh: bool = False
    mtf_is_fresh: bool = False
    invalidation_condition: str = ""


class SignalTracker:
    """Tracks signal outcomes and computes performance statistics."""

    def __init__(self) -> None:
        self.active_signals: dict[str, TrackedSignal] = {}
        self.history: list[dict] = []
        self._lifecycle_states: dict[tuple[str, str], dict[str, Any]] = {}
        self._lifecycle_listeners: set[Callable[[dict[str, Any]], None]] = set()
        self._load_history()

    def add_lifecycle_listener(
        self, listener: Callable[[dict[str, Any]], None]
    ) -> None:
        if not hasattr(self, "_lifecycle_listeners"):
            self._lifecycle_listeners = set()
        self._lifecycle_listeners.add(listener)

    def remove_lifecycle_listener(
        self, listener: Callable[[dict[str, Any]], None]
    ) -> None:
        self._lifecycle_listeners.discard(listener)

    def _notify_lifecycle_listeners(self, event: dict[str, Any]) -> None:
        for listener in tuple(getattr(self, "_lifecycle_listeners", ())):
            try:
                listener(dict(event))
            except Exception:
                logger.exception("Signal lifecycle listener failed")

    def _load_history(self) -> None:
        """Load signal history from disk."""
        data = safe_load_json(HISTORY_FILE, default=list)
        if isinstance(data, list):
            self.history = data
        else:
            self.history = []
        for item in self.history:
            if item.get("event_type") == "LIFECYCLE":
                key = (str(item.get("symbol", "")), str(item.get("timeframe", "")))
                if key[0] and key[1]:
                    self._lifecycle_states[key] = item

    def _save_history(self) -> None:
        """Persist signal history to disk atomically."""
        try:
            trade_history = [
                item for item in self.history if item.get("event_type") != "LIFECYCLE"
            ][-MAX_HISTORY:]
            lifecycle_history = [
                item for item in self.history if item.get("event_type") == "LIFECYCLE"
            ]
            trimmed = list(trade_history)
            lifecycle_capacity = MAX_HISTORY - len(trade_history)
            if lifecycle_capacity > 0:
                trimmed.extend(lifecycle_history[-lifecycle_capacity:])
            atomic_write_json(HISTORY_FILE, trimmed, backup=True)
        except Exception as exc:
            logger.warning("Failed to save signal history: %s", exc)

    def record_signal(self, signal: dict[str, Any]) -> str:
        """Record a new confirmed signal and return its ID."""
        now = int(time.time())
        sig_id = f"{signal.get('pair', 'UNK')}_{signal.get('timeframe', '15m')}_{now}"

        pair_val = signal.get("pair", "")
        tf_val = signal.get("timeframe", "15m")
        dir_val = signal.get("direction", "NEUTRAL")
        entry = float(signal.get("entry_price") or 0.0)
        sl = float(signal.get("stop_loss") or 0.0)
        plan_valid, plan_reason, risk_reward = validate_trade_plan(
            dir_val,
            entry,
            sl,
            signal.get("tp1"),
            signal.get("tp2"),
            signal.get("tp3"),
        )
        if not plan_valid:
            raise ValueError(f"Cannot record an invalid trade plan: {plan_reason}")
        if signal.get("market_data_fresh") is not True or signal.get("mtf_is_fresh") is not True:
            raise ValueError("Cannot record a signal with missing or stale market/MTF data.")
        try:
            cur_p = float(signal.get("current_price_at_signal") or 0.0)
            price_timestamp = int(signal.get("current_price_timestamp") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Cannot record a signal with an invalid current market price.") from exc
        if (
            not math.isfinite(cur_p)
            or cur_p <= 0
            or price_timestamp <= 0
            or now - price_timestamp < 0
            or now - price_timestamp > 15
        ):
            raise ValueError("Cannot record a signal with a stale market-price timestamp.")
        entry_range = signal.get("entry_range")
        try:
            range_values = [float(value) for value in entry_range]
        except (TypeError, ValueError) as exc:
            raise ValueError("Cannot record a signal without a valid entry range.") from exc
        if (
            not isinstance(entry_range, (list, tuple))
            or len(range_values) != 2
            or not all(math.isfinite(value) and value > 0 for value in range_values)
            or not min(range_values) <= entry <= max(range_values)
            or (dir_val == "LONG" and max(range_values) >= float(signal["tp1"]))
            or (dir_val == "SHORT" and min(range_values) <= float(signal["tp1"]))
        ):
            raise ValueError("Cannot record a signal without a valid entry range containing its entry.")

        # Deduplicate against both active tracked signals and recent history for same setup
        dedup_key = f"{pair_val}_{tf_val}_{dir_val}"
        if dedup_key in self.active_signals:
            existing = self.active_signals[dedup_key]
            if now - existing.created_at < 3600:
                return existing.signal_id
        entry_sig = round(entry, 6)
        for hist_item in reversed(self.history[-50:]):
            if (
                hist_item.get("symbol") == pair_val
                and hist_item.get("timeframe") == tf_val
                and hist_item.get("direction") == dir_val
                and round(float(hist_item.get("entry_price") or 0.0), 6) == entry_sig
                and (now - int(hist_item.get("created_at") or 0)) < 14400
            ):
                return str(hist_item.get("signal_id", sig_id))

        opposite_key = f"{pair_val}_{tf_val}_{'SHORT' if dir_val == 'LONG' else 'LONG'}"
        if opposite_key in self.active_signals:
            self._close_signal(opposite_key, entry, "INVALIDATED", 0.0)

        range_low, range_high = min(range_values), max(range_values)
        if (
            (dir_val == "LONG" and (cur_p <= sl or cur_p >= float(signal["tp1"])))
            or (dir_val == "SHORT" and (cur_p >= sl or cur_p <= float(signal["tp1"])))
        ):
            raise ValueError("Cannot record a signal after its stop or first target has already been crossed.")
        if (dir_val == "LONG" and cur_p < range_low) or (
            dir_val == "SHORT" and cur_p > range_high
        ):
            raise ValueError("Cannot record a signal after price has passed its entry range.")
        is_filled = range_low <= cur_p <= range_high

        tracked = TrackedSignal(
            signal_id=sig_id,
            symbol=pair_val,
            timeframe=tf_val,
            direction=dir_val,
            state="CONFIRMED" if is_filled else "WAITING_ENTRY_FILL",
            entry_price=entry,
            stop_loss=sl,
            tp1=float(signal.get("tp1") or 0.0),
            tp2=float(signal.get("tp2") or 0.0),
            tp3=float(signal.get("tp3") or 0.0),
            invalidation_level=float(signal.get("invalidation_level") or sl),
            setup_score=int(signal.get("setup_score") or 0),
            created_at=now,
            expires_at=int(signal.get("expires_at") or now + 3600),
            setup_state=str(signal.get("setup_state") or "CONFIRMED"),
            confirmed_at=now,
            reasons_for=signal.get("reasons_for", []),
            reasons_against=signal.get("reasons_against", []),
            htf_bias=signal.get("htf_bias", ""),
            strategy_type=signal.get("primary_model", ""),
            filled=is_filled,
            entry_range=range_values,
            risk_reward_ratio=round(risk_reward, 2),
            price_precision=(
                int(signal["price_precision"])
                if signal.get("price_precision") is not None
                else None
            ),
            mtf_reason=str(signal.get("mtf_reason") or ""),
            current_price=cur_p,
            current_price_timestamp=price_timestamp,
            market_data_fresh=True,
            mtf_is_fresh=True,
            invalidation_condition=str(signal.get("invalidation_condition") or ""),
        )
        if not is_filled:
            tracked.confirmed_at = None

        self.active_signals[dedup_key] = tracked
        if is_filled:
            lifecycle_state = f"CONFIRMED {dir_val}"
            lifecycle_reason = "New confirmed signal recorded."
        else:
            lifecycle_state = "WAITING_ENTRY_FILL"
            lifecycle_reason = "Signal is waiting for its entry price to be reached."
        self.record_lifecycle_state(
            pair_val,
            tf_val,
            lifecycle_state,
            dir_val,
            lifecycle_reason,
            event_transition="CONFIRMED" if is_filled else "NEW_SIGNAL",
        )
        return sig_id

    def record_lifecycle_state(
        self,
        symbol: str,
        timeframe: str,
        state: str,
        direction: str,
        reason: str,
        *,
        only_if_exists: bool = False,
        event_transition: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist a versioned lifecycle transition without changing trade records."""
        if not hasattr(self, "_lifecycle_states"):
            self._lifecycle_states = {}
        key = (symbol, timeframe)
        previous = self._lifecycle_states.get(key)
        if only_if_exists and previous is None:
            return None
        if (
            previous
            and previous.get("state") == state
            and previous.get("direction") == direction
            and previous.get("reason") == reason
        ):
            return previous

        if event_transition:
            transition = event_transition
        elif previous and previous.get("state") == state and previous.get("direction") == direction:
            transition = "UPDATED"
        elif state.startswith("CONFIRMED"):
            transition = "CONFIRMED"
        elif state.replace(" ", "_").upper() == "NO_TRADE":
            transition = "NO_TRADE"
        elif state in {"INVALIDATED", "EXPIRED"}:
            transition = state
        elif previous:
            transition = "UPDATED"
        else:
            transition = "NEW_SIGNAL"
        event = {
            "event_type": "LIFECYCLE",
            "symbol": symbol,
            "timeframe": timeframe,
            "state": state,
            "transition": transition,
            "direction": direction,
            "version": int(previous.get("version", 0)) + 1 if previous else 1,
            "updated_at": int(time.time()),
            "reason": reason,
        }
        self._lifecycle_states[key] = event
        self.history.append(event)
        self._save_history()
        self._notify_lifecycle_listeners(event)
        return event

    def expire_stale_setups(self, max_age_hours: int = 24) -> int:
        """Expire potential setups that have received no new evidence within the age limit."""
        cutoff = int(time.time()) - max_age_hours * 3600
        expired = 0
        for (symbol, timeframe), event in list(self._lifecycle_states.items()):
            if (
                event.get("state") in {"POTENTIAL", "SCANNING", "WAITING FOR CONFIRMATION"}
                and int(event.get("updated_at", 0)) < cutoff
            ):
                self.record_lifecycle_state(
                    symbol,
                    timeframe,
                    "EXPIRED",
                    str(event.get("direction", "NEUTRAL")),
                    "Setup expired after receiving no new market evidence.",
                )
                expired += 1
        return expired

    def get_active_setups(self) -> list[dict[str, Any]]:
        """Return potential and waiting setups, excluding terminal lifecycle states."""
        active_states = {"POTENTIAL", "SCANNING", "WAITING FOR CONFIRMATION"}
        return [
            dict(event)
            for event in self._lifecycle_states.values()
            if event.get("state") in active_states
        ]

    def update_signal(
        self,
        signal_id: str,
        current_price: float,
        price_timestamp: int | None = None,
    ) -> dict | None:
        """Check if a signal filled entry, hit TP, or hit SL given current price."""
        now = int(time.time())
        try:
            current_price = float(current_price)
            timestamp = int(price_timestamp or now)
        except (TypeError, ValueError):
            return None
        if (
            not math.isfinite(current_price)
            or current_price <= 0
            or timestamp <= 0
            or now - timestamp < 0
            or now - timestamp > 15
        ):
            return None
        for key, sig in list(self.active_signals.items()):
            if sig.signal_id != signal_id:
                continue

            sig.current_price = float(current_price)
            sig.current_price_timestamp = timestamp
            is_long = sig.direction == "LONG"
            entry = sig.entry_price
            risk = abs(entry - sig.stop_loss) or 1.0

            if not sig.filled:
                if len(sig.entry_range) != 2:
                    return None
                range_low, range_high = min(sig.entry_range), max(sig.entry_range)
                if (
                    (is_long and (current_price <= sig.stop_loss or current_price >= sig.tp1))
                    or (not is_long and (current_price >= sig.stop_loss or current_price <= sig.tp1))
                ):
                    return self._close_signal(key, current_price, "MISSED_ENTRY", 0.0)
                if range_low <= current_price <= range_high:
                    sig.filled = True
                    sig.state = "CONFIRMED"
                    sig.confirmed_at = now
                    self.record_lifecycle_state(
                        sig.symbol,
                        sig.timeframe,
                        f"CONFIRMED {sig.direction}",
                        sig.direction,
                        "Entry level was reached using a fresh market-price update.",
                        event_transition="CONFIRMED",
                    )
                elif (is_long and current_price < range_low) or (not is_long and current_price > range_high):
                    return self._close_signal(key, current_price, "MISSED_ENTRY", 0.0)
                else:
                    return None

            # Check SL hit
            if is_long and current_price <= sig.stop_loss:
                return self._close_signal(key, current_price, "HIT_SL", -1.0)
            if not is_long and current_price >= sig.stop_loss:
                return self._close_signal(key, current_price, "HIT_SL", -1.0)

            # Check TP hits
            if is_long:
                if sig.tp3 and current_price >= sig.tp3:
                    r = (current_price - entry) / risk
                    return self._close_signal(key, current_price, "HIT_TP3", r)
                if sig.tp2 and current_price >= sig.tp2:
                    r = (current_price - entry) / risk
                    return self._close_signal(key, current_price, "HIT_TP2", r)
                if sig.tp1 and current_price >= sig.tp1:
                    r = (current_price - entry) / risk
                    return self._close_signal(key, current_price, "HIT_TP1", r)
            else:
                if sig.tp3 and current_price <= sig.tp3:
                    r = (entry - current_price) / risk
                    return self._close_signal(key, current_price, "HIT_TP3", r)
                if sig.tp2 and current_price <= sig.tp2:
                    r = (entry - current_price) / risk
                    return self._close_signal(key, current_price, "HIT_TP2", r)
                if sig.tp1 and current_price <= sig.tp1:
                    r = (entry - current_price) / risk
                    return self._close_signal(key, current_price, "HIT_TP1", r)

        return None

    def expire_stale_signals(self, max_age_hours: int = 24) -> int:
        """Expire signals at their setup expiry or at the configured maximum age."""
        now = int(time.time())
        cutoff = now - (max_age_hours * 3600)
        expired_count = 0

        for key, sig in list(self.active_signals.items()):
            if sig.expires_at <= now or sig.created_at < cutoff:
                self._close_signal(key, sig.entry_price, "EXPIRED", 0.0)
                expired_count += 1

        return expired_count

    def _close_signal(
        self, key: str, close_price: float, reason: str, r_multiple: float
    ) -> dict:
        """Close a signal and move it to history."""
        sig = self.active_signals.pop(key, None)
        if not sig:
            return {}

        sig.state = reason
        sig.closed_at = int(time.time())
        sig.close_price = close_price
        sig.close_reason = reason
        sig.r_multiple = round(r_multiple, 2)

        # Calculate realized PnL %
        entry = sig.entry_price or 1.0
        if sig.direction == "LONG":
            sig.realized_pnl_pct = round(
                ((close_price - entry) / entry * 100) - sig.fees_pct - sig.slippage_pct,
                3,
            )
        else:
            sig.realized_pnl_pct = round(
                ((entry - close_price) / entry * 100) - sig.fees_pct - sig.slippage_pct,
                3,
            )

        record = asdict(sig)
        self.history.append(record)
        self._save_history()
        lifecycle_state = (
            "INVALIDATED"
            if reason in {"HIT_SL", "INVALIDATED"}
            else "EXPIRED"
            if reason in {"EXPIRED", "MISSED_ENTRY"}
            else "COMPLETED"
        )
        self.record_lifecycle_state(
            sig.symbol,
            sig.timeframe,
            lifecycle_state,
            sig.direction,
            f"Tracked trade closed: {reason}.",
            only_if_exists=True,
        )

        return record

    def get_active_signals(self) -> list[dict]:
        """Return all active signals as dicts."""
        return [asdict(s) for s in self.active_signals.values()]

    def get_history(
        self, symbol: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Return signal history, optionally filtered by symbol."""
        filtered = [item for item in self.history if item.get("event_type") != "LIFECYCLE"]
        if symbol:
            filtered = [h for h in filtered if h.get("symbol") == symbol]
        return filtered[-limit:]

    def get_performance_stats(self) -> dict[str, Any]:
        """Compute performance metrics, clearly separating tracked signals from completed trades."""
        active_count = len(self.active_signals)
        trade_history = [item for item in self.history if item.get("event_type") != "LIFECYCLE"]
        tracked_total = active_count + len(trade_history)

        if not trade_history:
            return {
                "tracked_signals_total": tracked_total,
                "active_tracked_signals": active_count,
                "expired_or_invalidated_signals": 0,
                "completed_trades": 0,
                "total_signals": tracked_total,
                "closed_signals": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": None,
                "win_rate_pct": 0.0,
                "avg_r_multiple": 0.0,
                "profit_factor": None,
                "max_drawdown_pct": 0.0,
                "longest_losing_streak": 0,
                "by_symbol": {},
                "by_timeframe": {},
                "by_strategy": {},
                "message": "No signal history recorded yet.",
            }

        completed_reasons = {"HIT_TP1", "HIT_TP2", "HIT_TP3", "HIT_SL", "MANUAL"}
        closed = [h for h in trade_history if h.get("close_reason") in completed_reasons]
        non_trade_closed = len(trade_history) - len(closed)

        if not closed:
            return {
                "tracked_signals_total": tracked_total,
                "active_tracked_signals": active_count,
                "expired_or_invalidated_signals": non_trade_closed,
                "completed_trades": 0,
                "total_signals": tracked_total,
                "closed_signals": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": None,
                "win_rate_pct": 0.0,
                "avg_r_multiple": 0.0,
                "profit_factor": None,
                "max_drawdown_pct": 0.0,
                "longest_losing_streak": 0,
                "by_symbol": {},
                "by_timeframe": {},
                "by_strategy": {},
                "message": "Tracked signals exist, but zero completed trades have closed at TP/SL yet.",
            }

        wins = [h for h in closed if h.get("r_multiple", 0) > 0]
        losses = [h for h in closed if h.get("r_multiple", 0) < 0]
        breakeven = [h for h in closed if h.get("r_multiple", 0) == 0]

        total = len(closed)
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = round(win_count / total * 100, 1) if total else None

        avg_r_multiple = round(sum(float(h.get("r_multiple", 0)) for h in closed) / total, 2) if total else 0.0
        avg_win_r = (
            round(sum(w["r_multiple"] for w in wins) / win_count, 2)
            if win_count
            else 0.0
        )
        avg_loss_r = (
            round(sum(lo["r_multiple"] for lo in losses) / loss_count, 2)
            if loss_count
            else 0.0
        )

        gross_profit = sum(w.get("realized_pnl_pct", 0) for w in wins)
        gross_loss = abs(sum(lo.get("realized_pnl_pct", 0) for lo in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None

        total_fees_pct = round(sum(float(h.get("fees_pct", 0.0)) for h in closed), 2)
        total_slippage_pct = round(sum(float(h.get("slippage_pct", 0.0)) for h in closed), 2)
        total_funding_pct = round(sum(float(h.get("funding_cost_pct", 0.0)) for h in closed), 2)

        # Max drawdown (sequential PnL)
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for h in closed:
            cumulative += h.get("realized_pnl_pct", 0)
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)

        # Longest losing streak
        current_streak = 0
        max_streak = 0
        for h in closed:
            if h.get("r_multiple", 0) < 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        def build_group_breakdown(key_field: str, default_val: str) -> dict[str, dict]:
            grp: dict[str, dict] = {}
            for h in closed:
                k = h.get(key_field) or default_val
                if k not in grp:
                    grp[k] = {"wins": 0, "losses": 0, "total_pnl_pct": 0.0}
                if h.get("r_multiple", 0) > 0:
                    grp[k]["wins"] += 1
                elif h.get("r_multiple", 0) < 0:
                    grp[k]["losses"] += 1
                grp[k]["total_pnl_pct"] += h.get("realized_pnl_pct", 0)
            for gd in grp.values():
                tot = gd["wins"] + gd["losses"]
                gd["win_rate"] = round(gd["wins"] / tot * 100, 1) if tot else 0.0
                gd["total_pnl_pct"] = round(gd["total_pnl_pct"], 2)
            return grp

        by_symbol = build_group_breakdown("symbol", "UNK")
        by_timeframe = build_group_breakdown("timeframe", "15m")
        by_strategy = build_group_breakdown("strategy_type", "MODEL_B")

        return {
            "tracked_signals_total": tracked_total,
            "active_tracked_signals": active_count,
            "expired_or_invalidated_signals": non_trade_closed,
            "completed_trades": total,
            "total_signals": tracked_total,
            "closed_signals": total,
            "wins": win_count,
            "losses": loss_count,
            "breakeven": len(breakeven),
            "win_rate": win_rate,
            "win_rate_pct": win_rate if win_rate is not None else 0.0,
            "avg_r_multiple": avg_r_multiple,
            "avg_win_r": avg_win_r,
            "avg_loss_r": avg_loss_r,
            "profit_factor": profit_factor,
            "max_drawdown_pct": round(max_dd, 2),
            "longest_losing_streak": max_streak,
            "gross_profit_pct": round(gross_profit, 2),
            "gross_loss_pct": round(gross_loss, 2),
            "net_pnl_pct": round(gross_profit - gross_loss, 2),
            "costs_summary": {
                "total_fees_pct": total_fees_pct,
                "total_slippage_pct": total_slippage_pct,
                "total_funding_pct": total_funding_pct,
            },
            "by_symbol": by_symbol,
            "by_timeframe": by_timeframe,
            "by_strategy": by_strategy,
            "is_sample_statistically_significant": total >= 30,
            "note": "Empirically measured statistics from completed trades only (tracked/unfilled/expired signals counted separately).",
        }


signal_tracker = SignalTracker()

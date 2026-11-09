"""Signal lifecycle tracker — records signal outcomes and performance metrics.

States: SCANNING → POTENTIAL → WAITING → CONFIRMED_LONG → CONFIRMED_SHORT → INVALIDATED → EXPIRED
Tracks: win/loss, R-multiple, PnL, fees, slippage, per-coin and per-timeframe statistics.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("signal_tracker")

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


class SignalTracker:
    """Tracks signal outcomes and computes performance statistics."""

    def __init__(self) -> None:
        self.active_signals: dict[str, TrackedSignal] = {}
        self.history: list[dict] = []
        self._load_history()

    def _load_history(self) -> None:
        """Load signal history from disk."""
        if HISTORY_FILE.exists():
            try:
                with HISTORY_FILE.open("r", encoding="utf-8") as f:
                    self.history = json.load(f)
            except Exception as exc:
                logger.warning("Failed to load signal history: %s", exc)
                self.history = []

    def _save_history(self) -> None:
        """Persist signal history to disk."""
        try:
            trimmed = self.history[-MAX_HISTORY:]
            with HISTORY_FILE.open("w", encoding="utf-8") as f:
                json.dump(trimmed, f, indent=2)
        except Exception as exc:
            logger.warning("Failed to save signal history: %s", exc)

    def record_signal(self, signal: dict[str, Any]) -> str:
        """Record a new confirmed signal and return its ID."""
        now = int(time.time())
        sig_id = f"{signal.get('pair', 'UNK')}_{signal.get('timeframe', '15m')}_{now}"

        # Deduplicate: don't re-record the same setup
        dedup_key = f"{signal.get('pair')}_{signal.get('timeframe')}_{signal.get('direction')}"
        if dedup_key in self.active_signals:
            existing = self.active_signals[dedup_key]
            if now - existing.created_at < 3600:  # within 1 hour
                return existing.signal_id

        entry = signal.get("entry_price", 0.0)
        sl = signal.get("stop_loss", 0.0)
        risk = abs(entry - sl) if entry and sl else 1.0

        tracked = TrackedSignal(
            signal_id=sig_id,
            symbol=signal.get("pair", ""),
            timeframe=signal.get("timeframe", "15m"),
            direction=signal.get("direction", "NEUTRAL"),
            state="CONFIRMED",
            entry_price=entry,
            stop_loss=sl,
            tp1=signal.get("tp1", 0.0),
            tp2=signal.get("tp2", 0.0),
            tp3=signal.get("tp3", 0.0),
            invalidation_level=signal.get("invalidation_level", sl),
            setup_score=signal.get("setup_score", 0),
            created_at=now,
            confirmed_at=now,
            reasons_for=signal.get("reasons_for", []),
            reasons_against=signal.get("reasons_against", []),
            htf_bias=signal.get("htf_bias", ""),
            strategy_type=signal.get("primary_model", ""),
        )

        self.active_signals[dedup_key] = tracked
        return sig_id

    def update_signal(self, signal_id: str, current_price: float) -> dict | None:
        """Check if a signal has hit TP or SL given current price."""
        for key, sig in list(self.active_signals.items()):
            if sig.signal_id != signal_id:
                continue

            is_long = sig.direction == "LONG"
            entry = sig.entry_price
            risk = abs(entry - sig.stop_loss) or 1.0

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
        """Expire signals older than max_age_hours."""
        now = int(time.time())
        cutoff = now - (max_age_hours * 3600)
        expired_count = 0

        for key, sig in list(self.active_signals.items()):
            if sig.created_at < cutoff:
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

        return record

    def get_active_signals(self) -> list[dict]:
        """Return all active signals as dicts."""
        return [asdict(s) for s in self.active_signals.values()]

    def get_history(
        self, symbol: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Return signal history, optionally filtered by symbol."""
        filtered = self.history
        if symbol:
            filtered = [h for h in filtered if h.get("symbol") == symbol]
        return filtered[-limit:]

    def get_performance_stats(self) -> dict[str, Any]:
        """Compute performance statistics from signal history."""
        if not self.history:
            return {
                "total_signals": 0,
                "message": "No signal history available.",
            }

        closed = [
            h for h in self.history if h.get("close_reason") not in ("", "EXPIRED")
        ]
        if not closed:
            return {
                "total_signals": len(self.history),
                "closed_signals": 0,
                "message": "No closed signals with outcomes.",
            }

        wins = [h for h in closed if h.get("r_multiple", 0) > 0]
        losses = [h for h in closed if h.get("r_multiple", 0) < 0]
        breakeven = [h for h in closed if h.get("r_multiple", 0) == 0]

        total = len(closed)
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = round(win_count / total * 100, 1) if total else 0.0

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
        profit_factor = (
            round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf")
        )

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

        # Per-symbol breakdown
        by_symbol: dict[str, dict] = {}
        for h in closed:
            sym = h.get("symbol", "UNK")
            if sym not in by_symbol:
                by_symbol[sym] = {"wins": 0, "losses": 0, "total_pnl_pct": 0.0}
            if h.get("r_multiple", 0) > 0:
                by_symbol[sym]["wins"] += 1
            elif h.get("r_multiple", 0) < 0:
                by_symbol[sym]["losses"] += 1
            by_symbol[sym]["total_pnl_pct"] += h.get("realized_pnl_pct", 0)

        for sym_data in by_symbol.values():
            total_sym = sym_data["wins"] + sym_data["losses"]
            sym_data["win_rate"] = (
                round(sym_data["wins"] / total_sym * 100, 1) if total_sym else 0.0
            )
            sym_data["total_pnl_pct"] = round(sym_data["total_pnl_pct"], 2)

        # Per-timeframe breakdown
        by_timeframe: dict[str, dict] = {}
        for h in closed:
            tf = h.get("timeframe", "15m")
            if tf not in by_timeframe:
                by_timeframe[tf] = {"wins": 0, "losses": 0, "total_pnl_pct": 0.0}
            if h.get("r_multiple", 0) > 0:
                by_timeframe[tf]["wins"] += 1
            elif h.get("r_multiple", 0) < 0:
                by_timeframe[tf]["losses"] += 1
            by_timeframe[tf]["total_pnl_pct"] += h.get("realized_pnl_pct", 0)

        for tf_data in by_timeframe.values():
            total_tf = tf_data["wins"] + tf_data["losses"]
            tf_data["win_rate"] = (
                round(tf_data["wins"] / total_tf * 100, 1) if total_tf else 0.0
            )
            tf_data["total_pnl_pct"] = round(tf_data["total_pnl_pct"], 2)

        return {
            "total_signals": len(self.history),
            "closed_signals": total,
            "wins": win_count,
            "losses": loss_count,
            "breakeven": len(breakeven),
            "win_rate_pct": win_rate,
            "avg_win_r": avg_win_r,
            "avg_loss_r": avg_loss_r,
            "profit_factor": profit_factor,
            "max_drawdown_pct": round(max_dd, 2),
            "longest_losing_streak": max_streak,
            "gross_profit_pct": round(gross_profit, 2),
            "gross_loss_pct": round(gross_loss, 2),
            "net_pnl_pct": round(gross_profit - gross_loss, 2),
            "by_symbol": by_symbol,
            "by_timeframe": by_timeframe,
            "note": "Statistics based on signal tracking. Not audited financial results.",
        }


signal_tracker = SignalTracker()

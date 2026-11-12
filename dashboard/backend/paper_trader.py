"""Simulated Paper Trading Engine for Dry Run."""

import logging
import math
import time
from pathlib import Path

from .config import config
from .risk_manager import validate_trade_plan
from .storage import atomic_write_json, safe_load_json

logger = logging.getLogger("trades")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
TRADES_FILE = DATA_DIR / "paper_trades.json"
if not TRADES_FILE.exists() and (Path(__file__).resolve().parent.parent / "paper_trades.json").exists():
    TRADES_FILE = Path(__file__).resolve().parent.parent / "paper_trades.json"


class PaperTrader:
    def __init__(self):
        self.balance = config.paper_balance
        self.positions: list[dict] = []
        self.trade_history: list[dict] = []
        self.load()

    def load(self):
        data = safe_load_json(TRADES_FILE, default=None)
        if isinstance(data, dict):
            self.balance = float(data.get("balance", config.paper_balance))
            self.positions = data.get("positions", [])
            self.trade_history = data.get("trade_history", [])
            logger.info("Loaded paper trading state: balance=$%.2f, open_positions=%d, trades=%d",
                        self.balance, len(self.positions), len(self.trade_history))

    def save(self):
        payload = {
            "balance": round(self.balance, 2),
            "positions": self.positions,
            "trade_history": self.trade_history[-100:],  # keep last 100
        }
        atomic_write_json(TRADES_FILE, payload, backup=True)

    def open_position(
        self,
        pair: str,
        side: str,
        amount_usdt: float,
        current_price: float,
        sl: float | None = None,
        tp: float | None = None,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        tp1: float | None = None,
        tp2: float | None = None,
        tp3: float | None = None,
        signal_id: str | None = None,
        timeframe: str | None = None,
        invalidation_condition: str = "",
    ) -> dict:
        actual_sl = sl if sl is not None else stop_loss
        side = side.upper()
        direction = "LONG" if side in {"BUY", "LONG"} else "SHORT" if side in {"SELL", "SHORT"} else ""
        if not math.isfinite(amount_usdt) or amount_usdt <= 0:
            return {"error": "Position size must be greater than zero"}
        if amount_usdt > self.balance:
            return {"error": f"Insufficient dry-run balance (${self.balance:.2f} available)"}

        if current_price <= 0:
            return {"error": "Invalid current price"}

        plan_valid, plan_reason, risk_reward = validate_trade_plan(
            direction,
            current_price,
            actual_sl,
            tp1,
            tp2,
            tp3,
        )
        if not plan_valid:
            return {"error": f"Paper trade rejected: {plan_reason}"}

        qty = amount_usdt / current_price
        pos_id = f"pos_{int(time.time() * 1000)}"
        position = {
            "id": pos_id,
            "pair": pair,
            "side": side,
            "direction": direction,
            "signal_id": signal_id,
            "timeframe": timeframe,
            "entry_price": current_price,
            "current_price": current_price,
            "amount_usdt": amount_usdt,
            "quantity": qty,
            "stop_loss": actual_sl,
            "take_profit": tp1,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "risk_reward_ratio": round(risk_reward, 2),
            "invalidation_condition": invalidation_condition,
            "open_time": int(time.time()),
            "pnl": 0.0,
            "pnl_pct": 0.0,
        }
        self.balance -= amount_usdt
        self.positions.append(position)
        self.save()
        logger.info("OPEN POSITION [%s] %s amount=$%.2f entry=%.2f sl=%s tp=%s (new_balance=$%.2f)",
                    side, pair, amount_usdt, current_price, position['stop_loss'], position['take_profit'], self.balance)
        return {"success": True, "position": position}

    def close_position(self, pos_id: str, current_price: float) -> dict:
        pos = next((p for p in self.positions if p["id"] == pos_id), None)
        if not pos:
            return {"error": "Position not found"}

        side = pos["side"]
        entry = pos["entry_price"]
        qty = pos["quantity"]

        if side == "BUY" or side == "LONG":
            profit = (current_price - entry) * qty
        else:
            profit = (entry - current_price) * qty

        return_amount = pos["amount_usdt"] + profit
        self.balance += max(0.0, return_amount)

        closed_trade = {
            **pos,
            "close_price": current_price,
            "close_time": int(time.time()),
            "realized_pnl": round(profit, 2),
            "realized_pnl_pct": round((profit / pos["amount_usdt"]) * 100, 2),
        }
        self.positions = [p for p in self.positions if p["id"] != pos_id]
        self.trade_history.append(closed_trade)
        self.save()
        logger.info("CLOSE POSITION [%s] %s exit=%.2f pnl=$%.2f (%.2f%%) (new_balance=$%.2f)",
                    side, pos['pair'], current_price, profit, closed_trade['realized_pnl_pct'], self.balance)
        return {"success": True, "status": "CLOSED", "closed_trade": closed_trade}

    def update_prices(self, current_prices: dict[str, float]):
        """Update unrealized PnL of active positions based on live prices."""
        for p in self.positions:
            pair = p["pair"]
            price = current_prices.get(pair)
            if price:
                p["current_price"] = price
                entry = p["entry_price"]
                qty = p["quantity"]
                if p["side"] in ["BUY", "LONG"]:
                    profit = (price - entry) * qty
                else:
                    profit = (entry - price) * qty
                p["pnl"] = round(profit, 2)
                p["pnl_pct"] = round((profit / p["amount_usdt"]) * 100, 2)

    def get_summary(self) -> dict:
        total_pnl = sum(p.get("pnl", 0) for p in self.positions)
        total_invested = sum(p.get("amount_usdt", 0) for p in self.positions)
        total_equity = self.balance + total_invested + total_pnl
        open_positions = [
            {
                **position,
                "mark_price": position.get("current_price", position.get("entry_price")),
                "unrealized_pnl": position.get("pnl", 0),
                "unrealized_pnl_pct": position.get("pnl_pct", 0),
            }
            for position in self.positions
        ]

        wins = sum(1 for t in self.trade_history if t.get("realized_pnl", 0) > 0)
        total_closed = len(self.trade_history)
        win_rate = round((wins / total_closed * 100), 1) if total_closed > 0 else 0.0
        realized_pnl = sum(t.get("realized_pnl", 0) for t in self.trade_history)

        return {
            "balance": round(self.balance, 2),
            "available_margin": round(self.balance, 2),
            "total_equity": round(total_equity, 2),
            "equity": round(total_equity, 2),
            "unrealized_pnl": round(total_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "total_invested": round(total_invested, 2),
            "open_positions_count": len(self.positions),
            "closed_trades_count": total_closed,
            "win_rate": win_rate,
            "positions": open_positions,
            "open_positions": open_positions,
            "history": self.trade_history[-15:],
        }


paper_trader = PaperTrader()

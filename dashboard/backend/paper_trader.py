"""Simulated Paper Trading Engine for Dry Run."""

import json
import time
from pathlib import Path

from .config import config


TRADES_FILE = Path(__file__).resolve().parent.parent / "paper_trades.json"


class PaperTrader:
    def __init__(self):
        self.balance = config.paper_balance
        self.positions: list[dict] = []
        self.trade_history: list[dict] = []
        self.load()

    def load(self):
        if TRADES_FILE.exists():
            try:
                with open(TRADES_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                    self.balance = float(data.get("balance", config.paper_balance))
                    self.positions = data.get("positions", [])
                    self.trade_history = data.get("trade_history", [])
            except Exception:
                pass

    def save(self):
        try:
            with open(TRADES_FILE, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "balance": round(self.balance, 2),
                        "positions": self.positions,
                        "trade_history": self.trade_history[-100:],  # keep last 100
                    },
                    f,
                    indent=2,
                )
        except Exception:
            pass

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
    ) -> dict:
        actual_sl = sl if sl is not None else stop_loss
        actual_tp = tp if tp is not None else take_profit
        side = side.upper()
        if amount_usdt > self.balance:
            return {"error": f"Insufficient dry-run balance (${self.balance:.2f} available)"}

        if current_price <= 0:
            return {"error": "Invalid current price"}

        qty = amount_usdt / current_price
        pos_id = f"pos_{int(time.time() * 1000)}"
        position = {
            "id": pos_id,
            "pair": pair,
            "side": side,
            "entry_price": current_price,
            "current_price": current_price,
            "amount_usdt": amount_usdt,
            "quantity": qty,
            "stop_loss": actual_sl or round(current_price * 0.965, 2),
            "take_profit": actual_tp or round(current_price * 1.05, 2),
            "open_time": int(time.time()),
            "pnl": 0.0,
            "pnl_pct": 0.0,
        }
        self.balance -= amount_usdt
        self.positions.append(position)
        self.save()
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

        wins = sum(1 for t in self.trade_history if t.get("realized_pnl", 0) > 0)
        total_closed = len(self.trade_history)
        win_rate = round((wins / total_closed * 100), 1) if total_closed > 0 else 0.0

        return {
            "balance": round(self.balance, 2),
            "total_equity": round(total_equity, 2),
            "unrealized_pnl": round(total_pnl, 2),
            "total_invested": round(total_invested, 2),
            "open_positions_count": len(self.positions),
            "closed_trades_count": total_closed,
            "win_rate": win_rate,
            "positions": self.positions,
            "history": self.trade_history[-15:],
        }


paper_trader = PaperTrader()

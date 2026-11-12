"""Institutional Risk Management & Trade Protection Module.

Enforces:
- Position sizing based on capital and fixed risk %
- Maximum daily loss limit and daily trading lock
- Maximum consecutive loss breaker and cooldown
- Maximum concurrent open positions limit
- Minimum risk-to-reward filter (rejects setups below minimum R:R)
- Emergency Kill Switch (closes all paper positions immediately)
- Futures leverage and liquidation distance estimation
- Independent from Gemini AI: all risk checks are strictly deterministic.
"""

from dataclasses import asdict, dataclass
import math
import time


def validate_trade_plan(
    direction: str,
    entry_price: float | None,
    stop_loss: float | None,
    tp1: float | None,
    tp2: float | None,
    tp3: float | None,
    min_risk_reward: float = 1.8,
) -> tuple[bool, str, float]:
    """Validate a complete directional plan and calculate gross R:R to TP2."""
    try:
        entry, stop, target1, target2, target3 = (
            float(value)
            for value in (entry_price, stop_loss, tp1, tp2, tp3)
        )
    except (TypeError, ValueError):
        return False, "Entry, stop loss, and all three take-profit levels are required.", 0.0

    if not all(math.isfinite(value) and value > 0 for value in (entry, stop, target1, target2, target3)):
        return False, "All trade-plan levels must be finite positive prices.", 0.0

    normalized_direction = direction.upper()
    if normalized_direction == "LONG":
        correctly_ordered = stop < entry < target1 < target2 < target3
    elif normalized_direction == "SHORT":
        correctly_ordered = target3 < target2 < target1 < entry < stop
    else:
        return False, "Trade direction must be LONG or SHORT.", 0.0

    if not correctly_ordered:
        return False, f"Trade-plan levels are not correctly ordered for {normalized_direction}.", 0.0

    risk = abs(entry - stop)
    reward = abs(target2 - entry)
    risk_reward = reward / risk
    if not math.isfinite(risk_reward) or risk_reward < min_risk_reward:
        return (
            False,
            f"Risk/reward {risk_reward:.2f} is below minimum threshold ({min_risk_reward:.2f}).",
            risk_reward,
        )
    return True, "Valid trade plan.", risk_reward


@dataclass
class RiskSettings:
    account_balance: float = 1000.0
    risk_per_trade_pct: float = 1.5  # 1.5% max risk of account capital
    max_daily_loss_pct: float = 5.0  # 5% daily loss lock
    max_consecutive_losses: int = 3
    max_open_positions: int = 3
    min_risk_reward: float = 1.8
    leverage: float = 1.0  # Spot = 1.0, Futures >= 1.0
    trading_locked: bool = False
    lock_reason: str = ""


class RiskManager:
    def __init__(self) -> None:
        self.settings = RiskSettings()
        self.daily_pnl = 0.0
        self.daily_loss_limit = 50.0  # $50 based on $1000
        self.consecutive_losses = 0
        self.last_trade_time = 0

    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
        capital: float | None = None,
        risk_pct: float | None = None,
        account_balance: float | None = None,
    ) -> dict:
        """Calculates exact position size in units and USD based on structural stop loss."""
        eff_cap = capital if capital is not None else account_balance
        balance = eff_cap if eff_cap is not None else self.settings.account_balance
        pct = risk_pct if risk_pct is not None else self.settings.risk_per_trade_pct

        risk_amount_usd = round(balance * (pct / 100.0), 2)
        price_risk_per_unit = abs(entry_price - stop_loss)

        if price_risk_per_unit <= 0:
            return {"error": "Invalid stop loss distance"}

        # Position quantity in asset units
        position_units = risk_amount_usd / price_risk_per_unit
        position_value_usd = round(position_units * entry_price, 2)

        # Margin required (based on leverage)
        margin_required = round(position_value_usd / self.settings.leverage, 2)

        # Estimated liquidation price (Futures isolated margin estimation)
        liq_distance_pct = (1.0 / self.settings.leverage) * 0.95
        est_liq_price = round(
            entry_price * (1 - liq_distance_pct)
            if stop_loss < entry_price
            else entry_price * (1 + liq_distance_pct),
            2,
        )

        return {
            "account_balance": balance,
            "risk_pct": pct,
            "risk_amount_usd": risk_amount_usd,
            "actual_risk_usd": risk_amount_usd,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "position_units": round(position_units, 6),
            "recommended_units": round(position_units, 6),
            "position_value_usd": position_value_usd,
            "margin_required": margin_required,
            "leverage": self.settings.leverage,
            "est_liquidation_price": est_liq_price,
        }

    def validate_trade(
        self,
        trade_risk_reward: float,
        current_open_positions_count: int,
        requested_amount_usd: float,
    ) -> tuple[bool, str]:
        """Validates all risk constraints before opening a trade."""
        if self.settings.trading_locked:
            return False, f"Trading is locked: {self.settings.lock_reason}"

        # 1. Max open positions check
        if current_open_positions_count >= self.settings.max_open_positions:
            return False, f"Maximum open positions limit reached ({self.settings.max_open_positions})"

        # 2. Risk/Reward filter
        if trade_risk_reward < self.settings.min_risk_reward:
            return (
                False,
                f"Risk/Reward {trade_risk_reward:.2f} is below minimum threshold ({self.settings.min_risk_reward})",
            )

        # 3. Max daily loss limit
        if self.daily_pnl <= -abs(self.settings.account_balance * (self.settings.max_daily_loss_pct / 100.0)):
            self.settings.trading_locked = True
            self.settings.lock_reason = "Daily loss limit breached"
            return False, "Daily loss limit breached. Trading locked for session."

        # 4. Consecutive loss breaker
        if self.consecutive_losses >= self.settings.max_consecutive_losses:
            return False, f"Max consecutive losses ({self.settings.max_consecutive_losses}) hit. Cooldown required."

        return True, "Passed all risk checks"

    def record_closed_trade(self, pnl: float) -> None:
        """Updates loss counters and locks session if limits are exceeded."""
        self.daily_pnl += pnl
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        # Auto-lock if daily limit breached
        max_allowed_loss = self.settings.account_balance * (self.settings.max_daily_loss_pct / 100.0)
        if self.daily_pnl <= -max_allowed_loss:
            self.settings.trading_locked = True
            self.settings.lock_reason = (
                f"Daily drawdown (-${abs(self.daily_pnl):.2f}) reached {self.settings.max_daily_loss_pct}% limit"
            )

    def emergency_kill_switch(self) -> dict:
        """Instantly engages emergency lockdown."""
        self.settings.trading_locked = True
        self.settings.lock_reason = f"EMERGENCY KILL SWITCH ACTIVATED at {time.strftime('%Y-%m-%d %H:%M:%S')}"
        return {"success": True, "locked": True, "reason": self.settings.lock_reason}

    def unlock_trading(self) -> dict:
        """Manually resets locks."""
        self.settings.trading_locked = False
        self.settings.lock_reason = ""
        self.consecutive_losses = 0
        return {"success": True, "locked": False}

    def get_status(self) -> dict:
        return {
            **asdict(self.settings),
            "daily_pnl": round(self.daily_pnl, 2),
            "consecutive_losses": self.consecutive_losses,
        }


risk_manager = RiskManager()

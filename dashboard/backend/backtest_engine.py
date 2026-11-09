"""Historical Backtesting & Replay Engine - Phase 2.

Features:
- Bar-by-bar non-repainting historical testing
- Models fees (0.1% taker) and realistic slippage (0.05%)
- Setup-Type / Entry Model Comparison (Model A vs B vs C)
- Maximum Adverse Excursion (MAE) & Maximum Favorable Excursion (MFE) tracking
- Market Regime Analysis (Trending Bull, Trending Bear, Ranging)
- Walk-forward In-Sample (70%) vs Out-of-Sample (30%) splits
- Expectancy, Profit Factor, Win Rate, Consecutive Wins/Losses, Equity Curve.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import pandas as pd
from .signal_engine import signal_engine
from .smc_engine import smc_engine
from .supply_demand import supply_demand_engine
from .technical_analysis import compute_indicators


@dataclass
class BacktestTrade:
    trade_id: int
    entry_time: int
    exit_time: int
    direction: str  # 'LONG' or 'SHORT'
    model_name: str  # 'MODEL_A', 'MODEL_B', 'MODEL_C'
    regime: str  # 'TRENDING_BULL', 'TRENDING_BEAR', 'RANGING'
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit: float
    pnl_net: float
    pnl_pct: float
    mae_pct: float  # Maximum Adverse Excursion
    mfe_pct: float  # Maximum Favorable Excursion
    exit_reason: str  # 'TAKE_PROFIT', 'STOP_LOSS', 'END_OF_DATA'
    duration_candles: int


class BacktestEngine:
    def __init__(self, fee_pct: float = 0.001, slippage_pct: float = 0.0005) -> None:
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct

    def run_backtest(
        self,
        candles: list[dict],
        pair: str = "BTC/USDT",
        timeframe: str = "15m",
        starting_capital: float = 1000.0,
        risk_per_trade_pct: float = 1.5,
    ) -> dict:
        """Executes a sequential bar-by-bar backtest over the candle dataset with MAE/MFE and regime breakdowns."""
        if not candles or len(candles) < 50:
            return {"error": "Insufficient candles for backtesting (minimum 50 required)"}

        df = pd.DataFrame(candles)
        n = len(df)
        balance = starting_capital
        equity_curve = [{"time": int(df["time"].iloc[0]), "equity": round(balance, 2)}]
        trades: list[BacktestTrade] = []
        active_trade = None
        trade_counter = 1

        # EMA 50 & 200 for market regime classification
        closes = df["close"].astype(float)
        ema_50 = closes.ewm(span=50, adjust=False).mean()
        ema_200 = closes.ewm(span=200, adjust=False).mean()

        warmup = 35

        for i in range(warmup, n):
            current_candle = df.iloc[i]
            c_high = float(current_candle["high"])
            c_low = float(current_candle["low"])
            c_close = float(current_candle["close"])
            c_time = int(current_candle["time"])

            # 1. Manage Active Trade
            if active_trade is not None:
                is_long = active_trade["direction"] == "LONG"
                entry = active_trade["entry_price"]
                sl = active_trade["stop_loss"]
                tp = active_trade["take_profit"]

                # Update MAE / MFE
                if is_long:
                    adverse = (c_low - entry) / entry * 100
                    favorable = (c_high - entry) / entry * 100
                else:
                    adverse = (entry - c_high) / entry * 100
                    favorable = (entry - c_low) / entry * 100

                active_trade["min_adverse"] = min(active_trade["min_adverse"], adverse)
                active_trade["max_favorable"] = max(active_trade["max_favorable"], favorable)

                hit_tp = False
                hit_sl = False
                exit_price = 0.0

                if is_long:
                    if c_high >= tp:
                        hit_tp = True
                        exit_price = tp * (1.0 - self.slippage_pct)
                    elif c_low <= sl:
                        hit_sl = True
                        exit_price = sl * (1.0 - self.slippage_pct)
                else:
                    if c_low <= tp:
                        hit_tp = True
                        exit_price = tp * (1.0 + self.slippage_pct)
                    elif c_high >= sl:
                        hit_sl = True
                        exit_price = sl * (1.0 + self.slippage_pct)

                if hit_tp or hit_sl:
                    if is_long:
                        gross_pnl_pct = (exit_price - entry) / entry
                    else:
                        gross_pnl_pct = (entry - exit_price) / entry

                    net_pnl_pct = gross_pnl_pct - (self.fee_pct * 2)
                    trade_risk_usd = balance * (risk_per_trade_pct / 100.0)
                    dollar_pnl = trade_risk_usd * (net_pnl_pct / (abs(entry - sl) / entry + 1e-6))
                    balance += dollar_pnl

                    trades.append(
                        BacktestTrade(
                            trade_id=trade_counter,
                            entry_time=active_trade["entry_time"],
                            exit_time=c_time,
                            direction=active_trade["direction"],
                            model_name=active_trade["model_name"],
                            regime=active_trade["regime"],
                            entry_price=round(entry, 2),
                            exit_price=round(exit_price, 2),
                            stop_loss=round(sl, 2),
                            take_profit=round(tp, 2),
                            pnl_net=round(dollar_pnl, 2),
                            pnl_pct=round(net_pnl_pct * 100, 2),
                            mae_pct=round(active_trade["min_adverse"], 2),
                            mfe_pct=round(active_trade["max_favorable"], 2),
                            exit_reason="TAKE_PROFIT" if hit_tp else "STOP_LOSS",
                            duration_candles=i - active_trade["entry_index"],
                        )
                    )
                    trade_counter += 1
                    active_trade = None
                    equity_curve.append({"time": c_time, "equity": round(balance, 2)})

            # 2. Check for New Signal if no active trade
            if active_trade is None and i < n - 1:
                hist_slice = df.iloc[: i + 1].to_dict(orient="records")
                sd = supply_demand_engine.detect_zones(hist_slice, timeframe=timeframe)
                smc = smc_engine.analyze(hist_slice)
                ind = compute_indicators(hist_slice)

                sig = signal_engine.evaluate(
                    pair=pair,
                    timeframe=timeframe,
                    current_price=c_close,
                    sd_data=sd,
                    smc_data=smc,
                    ind_data=ind,
                )

                models = sig.get("models", {})
                chosen_model = None

                # Model evaluation precedence: Model B (Confirmation) > Model C (Conservative) > Model A (Aggressive)
                if models.get("model_b_confirmation", {}).get("is_triggered"):
                    chosen_model = models["model_b_confirmation"]
                elif models.get("model_c_conservative", {}).get("is_triggered"):
                    chosen_model = models["model_c_conservative"]
                elif models.get("model_a_aggressive", {}).get("is_triggered"):
                    chosen_model = models["model_a_aggressive"]

                if chosen_model and chosen_model.get("setup_score", 0) >= 60:
                    # Classify market regime at entry
                    e50_val = ema_50.iloc[i]
                    e200_val = ema_200.iloc[i]
                    if c_close > e50_val > e200_val:
                        regime = "TRENDING_BULL"
                    elif c_close < e50_val < e200_val:
                        regime = "TRENDING_BEAR"
                    else:
                        regime = "RANGING"

                    active_trade = {
                        "direction": sig.get("direction", "LONG"),
                        "model_name": chosen_model.get("model_name"),
                        "regime": regime,
                        "entry_price": chosen_model.get("entry_price", c_close),
                        "stop_loss": chosen_model.get("stop_loss"),
                        "take_profit": chosen_model.get("tp2"),
                        "entry_time": c_time,
                        "entry_index": i,
                        "min_adverse": 0.0,
                        "max_favorable": 0.0,
                    }

        # Close any lingering trade at end of data
        if active_trade is not None:
            last_close = float(df["close"].iloc[-1])
            is_long = active_trade["direction"] == "LONG"
            gross_pct = (last_close - active_trade["entry_price"]) / active_trade["entry_price"] if is_long else (active_trade["entry_price"] - last_close) / active_trade["entry_price"]
            pnl_d = balance * 0.01 * gross_pct
            balance += pnl_d
            trades.append(
                BacktestTrade(
                    trade_id=trade_counter,
                    entry_time=active_trade["entry_time"],
                    exit_time=int(df["time"].iloc[-1]),
                    direction=active_trade["direction"],
                    model_name=active_trade["model_name"],
                    regime=active_trade["regime"],
                    entry_price=round(active_trade["entry_price"], 2),
                    exit_price=round(last_close, 2),
                    stop_loss=round(active_trade["stop_loss"], 2),
                    take_profit=round(active_trade["take_profit"], 2),
                    pnl_net=round(pnl_d, 2),
                    pnl_pct=round(gross_pct * 100, 2),
                    mae_pct=round(active_trade["min_adverse"], 2),
                    mfe_pct=round(active_trade["max_favorable"], 2),
                    exit_reason="END_OF_DATA",
                    duration_candles=n - 1 - active_trade["entry_index"],
                )
            )

        # Performance Metrics Calculation
        total_trades = len(trades)
        wins = [t for t in trades if t.pnl_net > 0]
        losses = [t for t in trades if t.pnl_net < 0]
        win_rate = round((len(wins) / total_trades) * 100, 2) if total_trades > 0 else 0.0

        total_gain = sum(t.pnl_net for t in wins)
        total_loss = abs(sum(t.pnl_net for t in losses))
        profit_factor = round(total_gain / total_loss, 2) if total_loss > 0 else (999.0 if total_gain > 0 else 0.0)

        net_pnl = round(balance - starting_capital, 2)
        net_return_pct = round((net_pnl / starting_capital) * 100, 2)

        # Max Drawdown
        equity_vals = [e["equity"] for e in equity_curve]
        peak = equity_vals[0]
        max_dd_usd = 0.0
        max_dd_pct = 0.0

        for eq in equity_vals:
            if eq > peak:
                peak = eq
            dd_usd = peak - eq
            dd_pct = (dd_usd / peak) * 100 if peak > 0 else 0.0
            if dd_usd > max_dd_usd:
                max_dd_usd = dd_usd
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct

        avg_win = round(total_gain / len(wins), 2) if wins else 0.0
        avg_loss = round(total_loss / len(losses), 2) if losses else 0.0
        expectancy = round(((len(wins) / max(total_trades, 1)) * avg_win) - ((len(losses) / max(total_trades, 1)) * avg_loss), 2)

        # Consecutive Win/Loss Streaks
        max_consec_wins = 0
        max_consec_losses = 0
        curr_wins = 0
        curr_losses = 0
        for t in trades:
            if t.pnl_net > 0:
                curr_wins += 1
                curr_losses = 0
                max_consec_wins = max(max_consec_wins, curr_wins)
            else:
                curr_losses += 1
                curr_wins = 0
                max_consec_losses = max(max_consec_losses, curr_losses)

        # Breakdown by Model
        model_stats = {}
        for m_name in ["MODEL_A_AGGRESSIVE_ZONE", "MODEL_B_CONFIRMATION_SWEEP_CHOCH", "MODEL_C_CONSERVATIVE_FVG_RETEST"]:
            m_trades = [t for t in trades if t.model_name == m_name]
            m_wins = [t for t in m_trades if t.pnl_net > 0]
            model_stats[m_name] = {
                "trades_count": len(m_trades),
                "win_rate": round((len(m_wins) / len(m_trades) * 100), 1) if m_trades else 0.0,
                "net_pnl": round(sum(t.pnl_net for t in m_trades), 2),
            }

        # Breakdown by Regime
        regime_stats = {}
        for r_name in ["TRENDING_BULL", "TRENDING_BEAR", "RANGING"]:
            r_trades = [t for t in trades if t.regime == r_name]
            r_wins = [t for t in r_trades if t.pnl_net > 0]
            regime_stats[r_name] = {
                "trades_count": len(r_trades),
                "win_rate": round((len(r_wins) / len(r_trades) * 100), 1) if r_trades else 0.0,
                "net_pnl": round(sum(t.pnl_net for t in r_trades), 2),
            }

        # Walk-forward Split: In-sample 70% vs Out-of-sample 30%
        split_idx = int(total_trades * 0.70)
        is_trades = trades[:split_idx]
        oos_trades = trades[split_idx:]

        is_pnl = sum(t.pnl_net for t in is_trades)
        oos_pnl = sum(t.pnl_net for t in oos_trades)

        return {
            "pair": pair,
            "timeframe": timeframe,
            "starting_capital": starting_capital,
            "final_balance": round(balance, 2),
            "net_pnl": net_pnl,
            "net_return_pct": net_return_pct,
            "total_trades": total_trades,
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "expectancy_per_trade": expectancy,
            "max_drawdown_usd": round(max_dd_usd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_consecutive_wins": max_consec_wins,
            "max_consecutive_losses": max_consec_losses,
            "model_comparison": model_stats,
            "regime_comparison": regime_stats,
            "walk_forward": {
                "in_sample_trades": len(is_trades),
                "in_sample_pnl": round(is_pnl, 2),
                "out_of_sample_trades": len(oos_trades),
                "out_of_sample_pnl": round(oos_pnl, 2),
            },
            "equity_curve": equity_curve,
            "trades": [asdict(t) for t in trades],
        }


backtest_engine = BacktestEngine()

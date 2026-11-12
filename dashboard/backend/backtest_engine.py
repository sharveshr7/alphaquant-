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
    r_multiple: float = 0.0
    fees_usd: float = 0.0
    slippage_usd: float = 0.0
    funding_usd: float = 0.0


class BacktestEngine:
    def __init__(self, fee_pct: float = 0.0005, slippage_pct: float = 0.0004, funding_8h_pct: float = 0.0001) -> None:
        self.fee_pct = fee_pct
        self.slippage_pct = slippage_pct
        self.funding_8h_pct = funding_8h_pct

    @staticmethod
    def _prec(val: float) -> int:
        aval = abs(val)
        if aval < 0.01:
            return 6
        if aval < 1.0:
            return 5
        if aval < 50.0:
            return 4
        return 2

    def run_backtest(
        self,
        candles: list[dict],
        pair: str = "BTC/USDT",
        timeframe: str = "15m",
        starting_capital: float = 1000.0,
        risk_per_trade_pct: float = 1.5,
    ) -> dict:
        """Executes a sequential bar-by-bar non-repainting backtest with conservative execution."""
        if not candles or len(candles) < 50:
            return {"error": "Insufficient candles for backtesting (minimum 50 required)"}

        tf_hours_map = {"1m": 1/60, "3m": 0.05, "5m": 1/12, "15m": 0.25, "30m": 0.5, "1h": 1.0, "2h": 2.0, "4h": 4.0, "6h": 6.0, "12h": 12.0, "1d": 24.0}
        bar_hours = tf_hours_map.get(timeframe, 0.25)

        df = pd.DataFrame(candles)
        candles_list = list(candles)
        n = len(df)
        split_bar_idx = int(n * 0.70)
        balance = starting_capital
        equity_curve = [{"time": int(df["time"].iloc[0]), "equity": round(balance, 2)}]
        trades: list[BacktestTrade] = []
        pending_signal = None
        active_trade = None
        trade_counter = 1

        closes = df["close"].astype(float)
        highs = df["high"].astype(float)
        lows = df["low"].astype(float)
        ema_50 = closes.ewm(span=50, adjust=False).mean()
        ema_200 = closes.ewm(span=200, adjust=False).mean()

        delta = closes.diff()
        avg_gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        avg_loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rsi_series = 100.0 - (100.0 / (1.0 + avg_gain / (avg_loss + 1e-10)))

        tr = pd.concat([
            highs - lows,
            (highs - closes.shift(1)).abs(),
            (lows - closes.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.ewm(alpha=1 / 14, adjust=False).mean()

        warmup = 35

        for i in range(warmup, n):
            current_candle = df.iloc[i]
            c_open = float(current_candle["open"])
            c_high = float(current_candle["high"])
            c_low = float(current_candle["low"])
            c_close = float(current_candle["close"])
            c_time = int(current_candle["time"])

            # 0. Execute pending signal at bar i OPEN (zero look-ahead)
            if active_trade is None and pending_signal is not None:
                is_long = pending_signal["direction"] == "LONG"
                fill_entry = c_open * (1.0 + self.slippage_pct) if is_long else c_open * (1.0 - self.slippage_pct)
                sl = float(pending_signal["stop_loss"])
                tp = float(pending_signal["take_profit"])
                valid_order = (is_long and sl < fill_entry < tp) or (not is_long and tp < fill_entry < sl)
                if valid_order:
                    active_trade = {
                        "direction": pending_signal["direction"],
                        "model_name": pending_signal["model_name"],
                        "regime": pending_signal["regime"],
                        "entry_price": fill_entry,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "entry_time": c_time,
                        "entry_index": i,
                        "min_adverse": 0.0,
                        "max_favorable": 0.0,
                    }
                pending_signal = None

            # 1. Manage Active Trade (check SL FIRST to eliminate intra-bar optimism)
            if active_trade is not None:
                is_long = active_trade["direction"] == "LONG"
                entry = active_trade["entry_price"]
                sl = active_trade["stop_loss"]
                tp = active_trade["take_profit"]

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
                    if c_low <= sl:
                        hit_sl = True
                        exit_price = sl * (1.0 - self.slippage_pct)
                    elif c_high >= tp:
                        hit_tp = True
                        exit_price = tp * (1.0 - self.slippage_pct)
                else:
                    if c_high >= sl:
                        hit_sl = True
                        exit_price = sl * (1.0 + self.slippage_pct)
                    elif c_low <= tp:
                        hit_tp = True
                        exit_price = tp * (1.0 + self.slippage_pct)

                if hit_tp or hit_sl:
                    if is_long:
                        gross_pnl_pct = (exit_price - entry) / entry
                    else:
                        gross_pnl_pct = (entry - exit_price) / entry

                    dur_bars = max(1, i - active_trade["entry_index"])
                    funding_periods = (dur_bars * bar_hours) / 8.0
                    funding_cost_pct = funding_periods * self.funding_8h_pct
                    net_pnl_pct = gross_pnl_pct - (self.fee_pct * 2) - funding_cost_pct

                    trade_risk_usd = balance * (risk_per_trade_pct / 100.0)
                    stop_dist_pct = max(abs(entry - sl) / entry, 1e-4)
                    notional_usd = trade_risk_usd / stop_dist_pct
                    fees_usd = round(notional_usd * (self.fee_pct * 2), 2)
                    slip_usd = round(notional_usd * (self.slippage_pct * 2), 2)
                    fund_usd = round(notional_usd * funding_cost_pct, 2)
                    r_mult = round(net_pnl_pct / stop_dist_pct, 2)
                    dollar_pnl = trade_risk_usd * (net_pnl_pct / stop_dist_pct)
                    balance = max(0.0, balance + dollar_pnl)

                    p_prec = self._prec(entry)
                    trades.append(
                        BacktestTrade(
                            trade_id=trade_counter,
                            entry_time=active_trade["entry_time"],
                            exit_time=c_time,
                            direction=active_trade["direction"],
                            model_name=active_trade["model_name"],
                            regime=active_trade["regime"],
                            entry_price=round(entry, p_prec),
                            exit_price=round(exit_price, p_prec),
                            stop_loss=round(sl, p_prec),
                            take_profit=round(tp, p_prec),
                            pnl_net=round(dollar_pnl, 2),
                            pnl_pct=round(net_pnl_pct * 100, 2),
                            mae_pct=round(active_trade["min_adverse"], 2),
                            mfe_pct=round(active_trade["max_favorable"], 2),
                            exit_reason="TAKE_PROFIT" if hit_tp else "STOP_LOSS",
                            duration_candles=dur_bars,
                            r_multiple=r_mult,
                            fees_usd=fees_usd,
                            slippage_usd=slip_usd,
                            funding_usd=fund_usd,
                        )
                    )
                    trade_counter += 1
                    active_trade = None
                    equity_curve.append({"time": c_time, "equity": round(balance, 2)})

            # 2. Check for New Signal strictly on completed history [:i+1], execute next bar
            if active_trade is None and pending_signal is None and i < n - 1:
                hist_slice = candles_list[max(0, i + 1 - 240) : i + 1]
                sd = supply_demand_engine.detect_zones(hist_slice, timeframe=timeframe)
                smc = smc_engine.analyze(hist_slice)
                ind = {
                    "latest": {
                        "ema_50": float(ema_50.iloc[i]),
                        "ema_200": float(ema_200.iloc[i]),
                        "atr": float(atr_series.iloc[i]),
                        "rsi": float(rsi_series.iloc[i]),
                    }
                }

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

                if models.get("model_b_confirmation", {}).get("is_triggered"):
                    chosen_model = models["model_b_confirmation"]
                elif models.get("model_c_conservative", {}).get("is_triggered"):
                    chosen_model = models["model_c_conservative"]
                elif models.get("model_a_aggressive", {}).get("is_triggered"):
                    chosen_model = models["model_a_aggressive"]

                if chosen_model and chosen_model.get("setup_score", 0) >= 60:
                    e50_val = ema_50.iloc[i]
                    e200_val = ema_200.iloc[i]
                    if c_close > e50_val > e200_val:
                        regime = "TRENDING_BULL"
                    elif c_close < e50_val < e200_val:
                        regime = "TRENDING_BEAR"
                    else:
                        regime = "RANGING"

                    pending_signal = {
                        "direction": sig.get("direction", "LONG"),
                        "model_name": chosen_model.get("model_name"),
                        "regime": regime,
                        "stop_loss": chosen_model.get("stop_loss"),
                        "take_profit": chosen_model.get("tp2"),
                    }

        # Close any lingering trade at end of data
        if active_trade is not None:
            last_close = float(df["close"].iloc[-1])
            entry = active_trade["entry_price"]
            is_long = active_trade["direction"] == "LONG"
            exit_p = last_close * (1.0 - self.slippage_pct) if is_long else last_close * (1.0 + self.slippage_pct)
            gross_pct = (exit_p - entry) / entry if is_long else (entry - exit_p) / entry
            dur_bars = max(1, n - 1 - active_trade["entry_index"])
            funding_cost_pct = ((dur_bars * bar_hours) / 8.0) * self.funding_8h_pct
            net_pct = gross_pct - (self.fee_pct * 2) - funding_cost_pct
            stop_dist_pct = max(abs(entry - active_trade["stop_loss"]) / entry, 1e-4)
            trade_risk_usd = balance * (risk_per_trade_pct / 100.0)
            notional_usd = trade_risk_usd / stop_dist_pct
            fees_usd = round(notional_usd * (self.fee_pct * 2), 2)
            slip_usd = round(notional_usd * (self.slippage_pct * 2), 2)
            fund_usd = round(notional_usd * funding_cost_pct, 2)
            r_mult = round(net_pct / stop_dist_pct, 2)
            pnl_d = trade_risk_usd * (net_pct / stop_dist_pct)
            balance = max(0.0, balance + pnl_d)
            p_prec = self._prec(entry)
            trades.append(
                BacktestTrade(
                    trade_id=trade_counter,
                    entry_time=active_trade["entry_time"],
                    exit_time=int(df["time"].iloc[-1]),
                    direction=active_trade["direction"],
                    model_name=active_trade["model_name"],
                    regime=active_trade["regime"],
                    entry_price=round(entry, p_prec),
                    exit_price=round(exit_p, p_prec),
                    stop_loss=round(active_trade["stop_loss"], p_prec),
                    take_profit=round(active_trade["take_profit"], p_prec),
                    pnl_net=round(pnl_d, 2),
                    pnl_pct=round(net_pct * 100, 2),
                    mae_pct=round(active_trade["min_adverse"], 2),
                    mfe_pct=round(active_trade["max_favorable"], 2),
                    exit_reason="END_OF_DATA",
                    duration_candles=dur_bars,
                    r_multiple=r_mult,
                    fees_usd=fees_usd,
                    slippage_usd=slip_usd,
                    funding_usd=fund_usd,
                )
            )

        # Chronological Out-of-Sample Walk-Forward split (70% / 30% by timestamp)
        split_ts = int(df["time"].iloc[split_bar_idx])
        is_trades = [t for t in trades if t.entry_time < split_ts]
        oos_trades = [t for t in trades if t.entry_time >= split_ts]

        # Performance Metrics Calculation
        total_trades = len(trades)
        wins = [t for t in trades if t.pnl_net > 0]
        losses = [t for t in trades if t.pnl_net < 0]
        win_rate = round((len(wins) / total_trades) * 100, 2) if total_trades > 0 else 0.0

        total_gain = sum(t.pnl_net for t in wins)
        total_loss = abs(sum(t.pnl_net for t in losses))
        profit_factor = round(total_gain / total_loss, 2) if total_loss > 0 else None
        avg_r = round(sum(t.r_multiple for t in trades) / total_trades, 2) if total_trades > 0 else 0.0
        total_fees_usd = round(sum(t.fees_usd for t in trades), 2)
        total_slip_usd = round(sum(t.slippage_usd for t in trades), 2)
        total_fund_usd = round(sum(t.funding_usd for t in trades), 2)

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

        # Walk-forward Split metrics (based on chronological 70% In-Sample / 30% Out-of-Sample candles)
        is_wins = [t for t in is_trades if t.pnl_net > 0]
        oos_wins = [t for t in oos_trades if t.pnl_net > 0]
        is_pnl = sum(t.pnl_net for t in is_trades)
        oos_pnl = sum(t.pnl_net for t in oos_trades)
        is_avg_r = round(sum(t.r_multiple for t in is_trades) / len(is_trades), 2) if is_trades else 0.0
        oos_avg_r = round(sum(t.r_multiple for t in oos_trades) / len(oos_trades), 2) if oos_trades else 0.0

        return {
            "pair": pair,
            "timeframe": timeframe,
            "candles_tested": n,
            "starting_capital": starting_capital,
            "final_balance": round(balance, 2),
            "net_pnl": net_pnl,
            "net_return_pct": net_return_pct,
            "total_trades": total_trades,
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "avg_r": avg_r,
            "avg_r_multiple": avg_r,
            "expectancy_per_trade": expectancy,
            "max_drawdown_usd": round(max_dd_usd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_consecutive_wins": max_consec_wins,
            "max_consecutive_losses": max_consec_losses,
            "cost_assumptions": {
                "fee_per_side_pct": round(self.fee_pct * 100, 3),
                "slippage_per_side_pct": round(self.slippage_pct * 100, 3),
                "funding_per_8h_pct": round(self.funding_8h_pct * 100, 3),
            },
            "costs_summary": {
                "total_fees_usd": total_fees_usd,
                "total_slippage_usd": total_slip_usd,
                "total_funding_usd": total_fund_usd,
                "total_costs_usd": round(total_fees_usd + total_slip_usd + total_fund_usd, 2),
                "round_trip_fee_pct": round(self.fee_pct * 200, 3),
                "round_trip_slippage_pct": round(self.slippage_pct * 200, 3),
            },
            "model_comparison": model_stats,
            "regime_comparison": regime_stats,
            "walk_forward": {
                "in_sample_trades": len(is_trades),
                "in_sample_win_rate": round(len(is_wins) / len(is_trades) * 100, 1) if is_trades else 0.0,
                "in_sample_pnl": round(is_pnl, 2),
                "in_sample_avg_r": is_avg_r,
                "out_of_sample_trades": len(oos_trades),
                "out_of_sample_win_rate": round(len(oos_wins) / len(oos_trades) * 100, 1) if oos_trades else 0.0,
                "out_of_sample_pnl": round(oos_pnl, 2),
                "out_of_sample_avg_r": oos_avg_r,
            },
            "equity_curve": equity_curve,
            "trades": [asdict(t) for t in trades],
        }


backtest_engine = BacktestEngine()

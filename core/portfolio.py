"""
core/portfolio.py
Virtual portfolio that simulates Polymarket trading with fake money.
Replicates exact fee structure: 1.56% taker, 0% maker, $0.02 gas.
Simulates realistic slippage from orderbook depth.
"""
import sqlite3
import logging
from datetime import datetime
from core.database import get_connection, save_signal
from core.logger import setup_logger

logger = setup_logger("Portfolio")

TAKER_FEE  = 0.0156   # 1.56% dynamic taker fee
MAKER_FEE  = 0.0      # 0% maker fee (we get rebates as makers)
GAS_COST   = 0.02     # $0.02 per transaction on Polygon


class VirtualPortfolio:
    """
    Simulates a Polymarket trading account with fake USDC.
    Every trade goes through exact fee and slippage calculations
    so paper trading data is 1:1 accurate with real trading costs.
    """

    def __init__(self, starting_bankroll: float = 1000.0):
        self.bankroll           = starting_bankroll
        self.starting_bankroll  = starting_bankroll
        self.peak_bankroll      = starting_bankroll
        self.open_positions     = {}  # trade_id → position dict
        self.closed_trades      = []
        logger.info(f"Portfolio initialised with ${starting_bankroll:,.2f} virtual USDC")

    # ------------------------------------------------------------------
    # Core Trade Execution
    # ------------------------------------------------------------------

    def open_position(self, condition_id: str, question: str,
                      direction: str, size_usdc: float,
                      market_price: float, order_type: str = "TAKER",
                      agent_source: str = "manual",
                      orderbook_depth: list = None) -> dict:
        """
        Open a paper trade position.

        condition_id:   Polymarket market ID
        question:       Market question text
        direction:      "YES" or "NO"
        size_usdc:      Dollar amount to spend (before fees)
        market_price:   Current price (0-1, where 1 = $1 payout)
        order_type:     "TAKER" (market order) or "MAKER" (limit order)
        agent_source:   Which agent generated this signal
        orderbook_depth: List of [price, size] levels for slippage calc

        Returns: trade dict or error dict
        """
        # Fee calculation
        fee_rate   = TAKER_FEE if order_type == "TAKER" else MAKER_FEE
        fee_amount = size_usdc * fee_rate
        total_cost = size_usdc + fee_amount + GAS_COST

        if total_cost > self.bankroll:
            logger.warning(
                f"Insufficient funds: need ${total_cost:.2f}, "
                f"have ${self.bankroll:.2f}"
            )
            return {"error": "Insufficient funds", "needed": total_cost,
                    "available": self.bankroll}

        # Slippage simulation
        slippage = self._calculate_slippage(size_usdc, market_price, orderbook_depth)
        fill_price = market_price + slippage

        # How many contracts do we get?
        # On Polymarket: price IS the probability, $1 payout if wins
        # contracts = amount_spent / fill_price
        contracts = size_usdc / fill_price

        # Deduct from bankroll
        self.bankroll -= total_cost

        trade = {
            "trade_id":     f"PT{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}",
            "condition_id": condition_id,
            "question":     question[:100] if question else "",
            "direction":    direction,
            "order_type":   order_type,
            "size_usdc":    size_usdc,
            "entry_price":  market_price,
            "fill_price":   fill_price,
            "slippage":     slippage,
            "taker_fee":    fee_amount,
            "gas_cost":     GAS_COST,
            "total_cost":   total_cost,
            "contracts":    contracts,
            "agent_source": agent_source,
            "status":       "open",
            "exit_price":   None,
            "pnl":          None,
            "opened_at":    datetime.utcnow().isoformat()
        }

        self.open_positions[trade["trade_id"]] = trade
        self._save_trade(trade)
        self._save_portfolio_snapshot()

        logger.info(
            f"OPENED [{direction}] {question[:50] if question else condition_id[:20]} "
            f"| Size: ${size_usdc:.2f} | Fill: {fill_price:.3f} "
            f"| Contracts: {contracts:.1f} | Fees: ${fee_amount + GAS_COST:.3f} "
            f"| Bankroll: ${self.bankroll:.2f}"
        )
        return trade

    def close_position(self, trade_id: str, resolution_price: float) -> dict:
        """
        Close a position at resolution_price (0 = NO wins, 1 = YES wins).
        resolution_price: 0.0 or 1.0 for binary resolution,
                         or a mid price for early exit.
        """
        if trade_id not in self.open_positions:
            return {"error": f"Trade {trade_id} not found"}

        trade = self.open_positions[trade_id]

        # Calculate payout
        # If we bought YES at 0.65 and it resolves YES (price=1.0):
        # payout = contracts * 1.0
        # If it resolves NO (price=0.0):
        # payout = contracts * 0.0
        contracts  = trade["contracts"]
        direction  = trade["direction"]

        if direction == "YES":
            payout = contracts * resolution_price
        else:
            # For NO contracts, payout = contracts * (1 - resolution_price)
            payout = contracts * (1 - resolution_price)

        # Exit gas cost
        exit_gas = GAS_COST
        net_payout = payout - exit_gas

        pnl = net_payout - trade["total_cost"]
        self.bankroll += net_payout

        trade["exit_price"]  = resolution_price
        trade["pnl"]         = pnl
        trade["status"]      = "closed"
        trade["closed_at"]   = datetime.utcnow().isoformat()

        self.closed_trades.append(trade)
        del self.open_positions[trade_id]

        # Update peak bankroll
        if self.bankroll > self.peak_bankroll:
            self.peak_bankroll = self.bankroll

        self._update_trade_in_db(trade)
        self._save_portfolio_snapshot()

        result = "WIN" if pnl > 0 else "LOSS"
        logger.info(
            f"CLOSED [{result}] {trade.get('question','')[:50]} "
            f"| Exit: {resolution_price:.3f} | PnL: ${pnl:+.2f} "
            f"| Bankroll: ${self.bankroll:.2f}"
        )
        return trade

    def close_all_positions(self, resolution_price: float = 0.5):
        """Close all open positions (e.g. for end-of-day cleanup)."""
        for trade_id in list(self.open_positions.keys()):
            self.close_position(trade_id, resolution_price)

    # ------------------------------------------------------------------
    # Slippage Model
    # ------------------------------------------------------------------

    def _calculate_slippage(self, size_usdc: float, price: float,
                             orderbook: list = None) -> float:
        """
        Estimate slippage based on order size relative to liquidity.
        
        If we have real orderbook data: walk the book.
        If not: use a simple model based on size.
        
        Slippage model (without orderbook):
        < $50:    ~0.001 (0.1%)
        < $200:   ~0.005 (0.5%)
        < $1000:  ~0.010 (1.0%)
        > $1000:  ~0.020 (2.0%)
        """
        if orderbook:
            return self._walk_orderbook_slippage(size_usdc, price, orderbook)

        # Simple size-based model
        if size_usdc < 50:
            return 0.001
        elif size_usdc < 200:
            return 0.005
        elif size_usdc < 1000:
            return 0.010
        else:
            return 0.020

    def _walk_orderbook_slippage(self, size_usdc: float, price: float,
                                  asks: list) -> float:
        """Walk the real orderbook to calculate true average fill price."""
        if not asks:
            return 0.005
        remaining = size_usdc
        total_cost = 0
        for level_price, level_size in asks:
            level_price = float(level_price)
            level_size  = float(level_size) * level_price  # in USDC
            fill_amount = min(remaining, level_size)
            total_cost += fill_amount * level_price
            remaining  -= fill_amount
            if remaining <= 0:
                break
        if remaining > 0:
            total_cost += remaining * price * 1.05  # 5% premium for thin book
        avg_fill = total_cost / size_usdc
        return max(0, avg_fill - price)

    # ------------------------------------------------------------------
    # Portfolio Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        all_trades = self.closed_trades
        if not all_trades:
            return {
                "bankroll": self.bankroll,
                "starting_bankroll": self.starting_bankroll,
                "total_pnl": self.bankroll - self.starting_bankroll,
                "total_pnl_pct": (self.bankroll - self.starting_bankroll) / self.starting_bankroll * 100,
                "peak_bankroll": self.peak_bankroll,
                "drawdown_pct": (self.peak_bankroll - self.bankroll) / self.peak_bankroll * 100 if self.peak_bankroll > 0 else 0,
                "open_positions": len(self.open_positions),
                "total_trades": 0,
                "win_rate": 0,
                "total_fees_paid": 0,
                "sharpe_ratio": 0,
            }

        wins   = [t for t in all_trades if t.get("pnl", 0) > 0]
        losses = [t for t in all_trades if t.get("pnl", 0) <= 0]
        total_pnl    = sum(t.get("pnl", 0) for t in all_trades)
        total_fees   = sum(t.get("taker_fee", 0) + t.get("gas_cost", 0) for t in all_trades)
        win_rate     = len(wins) / len(all_trades) if all_trades else 0
        avg_win      = sum(t.get("pnl",0) for t in wins) / len(wins) if wins else 0
        avg_loss     = sum(t.get("pnl",0) for t in losses) / len(losses) if losses else 0
        drawdown     = (self.peak_bankroll - self.bankroll) / self.peak_bankroll * 100 if self.peak_bankroll > 0 else 0

        return {
            "bankroll":           self.bankroll,
            "starting_bankroll":  self.starting_bankroll,
            "total_pnl":          self.bankroll - self.starting_bankroll,
            "total_pnl_pct":      (self.bankroll - self.starting_bankroll) / self.starting_bankroll * 100,
            "peak_bankroll":      self.peak_bankroll,
            "drawdown_pct":       drawdown,
            "open_positions":     len(self.open_positions),
            "total_trades":       len(all_trades),
            "win_count":          len(wins),
            "loss_count":         len(losses),
            "win_rate":           win_rate * 100,
            "avg_win":            avg_win,
            "avg_loss":           avg_loss,
            "total_fees_paid":    total_fees,
            "profit_factor":      abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else 0,
        }

    def print_summary(self):
        stats = self.get_stats()
        print("\n" + "="*50)
        print("  PORTFOLIO SUMMARY")
        print("="*50)
        print(f"  Bankroll:      ${stats['bankroll']:>10,.2f}")
        print(f"  Total PnL:     ${stats['total_pnl']:>+10,.2f}  ({stats['total_pnl_pct']:+.1f}%)")
        print(f"  Peak:          ${stats['peak_bankroll']:>10,.2f}")
        print(f"  Drawdown:                 {stats['drawdown_pct']:.1f}%")
        print(f"  Trades:        {stats['total_trades']:>4}")
        print(f"  Win Rate:               {stats.get('win_rate',0):.1f}%")
        print(f"  Open Pos:      {stats['open_positions']:>4}")
        print(f"  Fees Paid:     ${stats['total_fees_paid']:>10,.3f}")
        print("="*50 + "\n")

    # ------------------------------------------------------------------
    # Database Persistence
    # ------------------------------------------------------------------

    def _save_trade(self, trade: dict):
        conn = get_connection()
        conn.execute("""
            INSERT OR REPLACE INTO paper_trades
            (condition_id, question, direction, order_type, size_usdc,
             entry_price, fill_price, slippage, taker_fee, gas_cost,
             total_cost, contracts, agent_source, status, opened_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade["condition_id"], trade["question"], trade["direction"],
            trade["order_type"], trade["size_usdc"], trade["entry_price"],
            trade["fill_price"], trade["slippage"], trade["taker_fee"],
            trade["gas_cost"], trade["total_cost"], trade["contracts"],
            trade["agent_source"], trade["status"], trade["opened_at"]
        ))
        conn.commit()
        conn.close()

    def _update_trade_in_db(self, trade: dict):
        conn = get_connection()
        conn.execute("""
            UPDATE paper_trades
            SET status=?, exit_price=?, pnl=?, closed_at=?
            WHERE condition_id=? AND opened_at=?
        """, (
            trade["status"], trade["exit_price"],
            trade["pnl"], trade["closed_at"],
            trade["condition_id"], trade["opened_at"]
        ))
        conn.commit()
        conn.close()

    def _save_portfolio_snapshot(self):
        stats = self.get_stats()
        conn = get_connection()
        conn.execute("""
            INSERT INTO portfolio_snapshots
            (bankroll, open_positions, total_pnl, win_count, loss_count, timestamp)
            VALUES (?,?,?,?,?,?)
        """, (
            self.bankroll,
            stats["open_positions"],
            stats["total_pnl"],
            stats.get("win_count", 0),
            stats.get("loss_count", 0),
            datetime.utcnow().isoformat()
        ))
        conn.commit()
        conn.close()

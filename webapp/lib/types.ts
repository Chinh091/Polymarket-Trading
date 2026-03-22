export interface Trade {
  id: number;
  condition_id: string;
  question: string;
  direction: string;
  order_type: string;
  size_usdc: number;
  entry_price: number;
  fill_price: number;
  slippage: number;
  taker_fee: number;
  gas_cost: number;
  total_cost: number;
  contracts: number;
  agent_source: string;
  status: "open" | "closed";
  exit_price?: number;
  pnl?: number;
  closed_at?: string;
  opened_at: string;
}

export interface PortfolioSnapshot {
  id: number;
  bankroll: number;
  open_positions: number;
  total_pnl: number;
  win_count: number;
  loss_count: number;
  timestamp: string;
}

export interface AgentSignal {
  id: number;
  agent_name: string;
  condition_id: string;
  signal_type: string;
  direction: string;
  confidence: number;
  edge_pct: number;
  raw_data: Record<string, unknown>;
  timestamp: string;
}

export interface JournalEntry {
  id: number;
  condition_id: string;
  question: string;
  direction: string;
  proposed_size: number;
  entry_price: number;
  agent_sources: string;
  agent_signals: unknown[];
  opus_verdict: string;
  opus_reasoning: string;
  outcome: "executed" | "rejected_opus" | "rejected_risk";
  avg_edge: number;
  avg_confidence: number;
  market_volume: number;
  logged_at: string;
}

export interface StrategyParam {
  param_key: string;
  param_value: number;
  previous_value?: number;
  reason?: string;
  updated_at: string;
}

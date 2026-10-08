export type DeployMode = 'REAL' | 'SHADOW' | 'SIMULATION';

export type ScheduleType = 'interval' | 'weekly';
export type TradeWeekday = 'MON' | 'TUE' | 'WED' | 'THU' | 'FRI';
export type TradingSession = 'AM' | 'PM';
export type LiveOrderType = 'LIMIT' | 'MARKET';

export interface ExecutionConfig {
  max_buy_drop?: number;
  stop_loss?: number;
}

export interface LiveTradeConfig {
  rebalance_days?: 1 | 3 | 5 | 10 | 20;
  schedule_type: ScheduleType;
  trade_weekdays?: TradeWeekday[];
  enabled_sessions: TradingSession[];
  sell_time: string;
  buy_time: string;
  sell_first: boolean;
  order_type: LiveOrderType;
  max_price_deviation?: number;
  max_orders_per_cycle: number;
}

export interface StrategyLiveDefaults {
  execution_defaults?: ExecutionConfig;
  live_defaults?: Partial<LiveTradeConfig>;
  live_config_tips?: string[];
}


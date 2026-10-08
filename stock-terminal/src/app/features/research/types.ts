export type SignalType = 'buy' | 'hold' | 'sell';
export type ConfidenceLevel = 'high' | 'medium' | 'watch';
export type SortKey = 'score' | 'limitUp' | 'turnover' | 'amount' | 'return1d' | 'return20d' | 'volStd20' | 'mainFlow';
export type FilterSectionKey =
  | 'common'
  | 'market'
  | 'momentum'
  | 'volatility'
  | 'technical'
  | 'fundFlow'
  | 'style'
  | 'industry'
  | 'chip'
  | 'fundamental'
  | 'sector';
export type DataSourceTab = 'candidates' | 'watchlist' | 'pool';

export interface ResearchModelOption {
  modelId: string;
  name: string;
  style: string;
  description: string;
}

export interface WatchlistRow {
  key: string;
  symbol: string;
  stockName: string | null;
  addedAt: string | null;
  sourceRunId: string | null;
  notes: string | null;
  tags: string[];
}

export interface ResearchPoolRow {
  key: string;
  symbol: string;
  stockName: string | null;
  addedAt: string | null;
  sourceRunId: string | null;
  modelId: string | null;
  fusionScore: number | null;
  thesisSummary: string | null;
  status: string;
  notes: string | null;
  tags: string[];
}

export interface ResearchStockRow {
  key: string;
  modelId: string;
  runId: string;
  rank: number;
  code: string;
  name: string;
  score: number;
  latestChange: number;
  totalReturn?: number | null;
  consecutiveLimitUpDays: number;
  volumeTrend3d: number | null;
  volumeTrend5d: boolean;
  turnoverRate: number | null;
  amount: number | null;
  marketCap?: number;
  sector: string;
  concept: string;
  signal?: SignalType;
  closePrice?: number;
  pe?: number;
  roe?: number;
  profitGrowth?: number;
  rsi?: number;
  mainFlow?: number;
  flowNetAmount?: number;
  instOwnership?: number;
  ma5?: number;
  ma10?: number;
  ma20?: number;
  ma60?: number;
  maGap5?: number;
  maGap10?: number;
  maGap20?: number;
  volRatio5?: number;
  volRatio20?: number;
  return1d?: number;
  return3d?: number;
  return5d?: number;
  return10d?: number;
  return20d?: number;
  return60d?: number;
  pb?: number;
  psTtm?: number;
  totalMv?: number;
  floatMv?: number;
  listedDays?: number;
  rsi14?: number;
  atr?: number;
  macdHist?: number;
  conceptTags?: string[];
  indexTags?: string[];
  riskFlags: string[];
  hitReasons?: string[];
  volumeBars?: number[];
  thesis: string;
  confidence?: ConfidenceLevel;
  isMatched?: boolean;
  isSt?: boolean;
  isTradable?: boolean;
  isHs300?: boolean;
  isCsi500?: boolean;
  isCsi1000?: boolean;
  // --- Momentum ---
  momRet1d?: number;
  momRet3d?: number;
  momRet5d?: number;
  momRet10d?: number;
  momRet20d?: number;
  momRet60d?: number;
  momRet120d?: number;
  momEmaGap12?: number;
  momEmaGap26?: number;
  momMacdDif?: number;
  momMacdDea?: number;
  momKdjK?: number;
  momKdjD?: number;
  momKdjJ?: number;
  momRoc12?: number;
  // --- Volatility ---
  volStd5?: number;
  volStd10?: number;
  volStd20?: number;
  volStd60?: number;
  volAtr14?: number;
  volAtr20?: number;
  volTrueRange?: number;
  volParkinson10?: number;
  volParkinson20?: number;
  volGk20?: number;
  volUpDownRatio?: number;
  volSkew?: number;
  volRealizedRv?: number;
  volRealizedRrv?: number;
  volRealizedRskew?: number;
  // --- Liquidity ---
  liqTurnoverOs?: number;
  liqTurnoverTl?: number;
  liqVolume?: number;
  liqVolumeMa5?: number;
  liqVolumeMa20?: number;
  liqVolumeRatio5?: number;
  liqVolumeRatio20?: number;
  liqAmountMa5?: number;
  liqAmountMa20?: number;
  liqAmountRatio5?: number;
  liqObv20?: number;
  liqMfi14?: number;
  liqAmihud20?: number;
  // --- Fund Flow ---
  flowBuyAmount?: number;
  flowSellAmount?: number;
  flowNetRatio?: number;
  flowLargeNet?: number;
  flowMediumNet?: number;
  flowSmallNet?: number;
  flowLargeRatio?: number;
  flowMediumRatio?: number;
  flowSmallRatio?: number;
  flowImbalanceVolume?: number;
  flowMoneyFlowIndex?: number;
  flowBigTradeRatio?: number;
  // --- Style ---
  styleBeta20?: number;
  styleBeta60?: number;
  styleBeta120?: number;
  styleIdioVol20?: number;
  styleIdioVol60?: number;
  styleResidualRet20?: number;
  styleSize20?: number;
  styleValue20?: number;
  styleMvRank?: number;
  styleValueZscore?: number;
  // --- Industry ---
  indRet5?: number;
  indRet10?: number;
  indRet20?: number;
  indStrength20?: number;
  indStrength60?: number;
  indDispersion20?: number;
  indBreadthUp20?: number;
  indVolumeRatio20?: number;
  indCrowding20?: number;
  indRotationSpeed20?: number;
  indRelativePe?: number;
  indConcentration?: number;
  indRelativeMomentum20?: number;
  // --- Chip ---
  chipProfitRatio20?: number;
  chipProfitRatio60?: number;
  chipProfitRatio120?: number;
  chipConcentration20?: number;
  chipPeakDistance?: number;
  conceptVolumeRatio?: number;
  chipCost90Width?: number;
  chipProfitDelta5?: number;
  // --- Concept ---
  conceptHotScore?: number;
  conceptMomentumTop3?: number;
  conceptExposureTop1?: number;
  conceptRotationScore?: number;
  conceptCrowdingMax?: number;
  conceptDiversity?: number;

  conceptLeaderScore?: number;
  // --- Technical Extended ---
  techBbWidth?: number;
  techBbPos?: number;
  techCci20?: number;
  techAdx14?: number;
  techVolPriceCorr20?: number;
  // --- Sentiment ---
  sentimentLiquidityScore?: number;
  sentimentBuyPressure?: number;
  sentimentSellPressure?: number;
  sentimentBodyRatio?: number;
  sentimentIntradayVol?: number;
  sentimentGapUpDown?: number;
  sentimentAmPmTrend?: number;
  sentimentVolumeConcentration?: number;
  // --- Microstructure (l2) ---
  microVpin8?: number;
  microVpin20?: number;
  microVpin50?: number;
  microVpinMa5?: number;
  microVpinMa20?: number;
  microPin?: number;
  microOrderFlowToxicity?: number;
  microQspEqual?: number;
  microEspEqual?: number;
  microAmihudIlliquidity?: number;
  microKyleLambda?: number;
  microPriceImpactLarge?: number;
  microJumpFlag?: number;
  microJumpCount1pct?: number;
  microDepthBid?: number;
  microDepthAsk?: number;
  microDepthRatio1?: number;
  microDepthImbalance1?: number;
  microTradeBuyPressure?: number;
  microTradeSellPressure?: number;
  microRealizedSpread?: number;
  microRealizedRrv?: number;
  microRealizedRskew?: number;
}

/** 后端 /research/features/{symbol} 返回的分类字段包（QuantDB parquet 全字段） */
export type QuantDbFeatureCategory =
  | 'valuation'
  | 'technical'
  | 'momentum'
  | 'volatility'
  | 'liquidity'
  | 'fundFlow'
  | 'fundamental'
  | 'style'
  | 'industry'
  | 'chip'
  | 'concept'
  | 'microstructure'
  | 'sentiment'
  | 'other';

export type QuantDbFeatureValues = Record<string, number | string | boolean | null>;

export type QuantDbFeatures = {
  symbol: string;
  tradeDate: string | null;
  sources: string[];
} & Partial<Record<QuantDbFeatureCategory, QuantDbFeatureValues>>;

export interface ResearchFiltersState {
  // Core
  minScore: number;
  limitUpDays: number;
  excludeSt: boolean;
  highConfidenceOnly: boolean;
  // Market & Liquidity
  amountRange: [number, number];
  turnoverRange: [number, number];
  totalMvRange: [number, number];
  floatMvRange: [number, number];
  // Momentum
  return1dRange: [number, number];
  return3dRange: [number, number];
  return5dRange: [number, number];
  return10dRange: [number, number];
  return20dRange: [number, number];
  return60dRange: [number, number];
  maGap5Range: [number, number];
  maGap10Range: [number, number];
  maGap20Range: [number, number];
  rsiRange: [number, number];
  rsi14Range: [number, number];
  kdjKRange: [number, number];
  macdHistRange: [number, number];
  breakout20dRange: [number, number];
  // Volatility
  volStd5Range: [number, number];
  volStd20Range: [number, number];
  volStd60Range: [number, number];
  atr14Range: [number, number];
  volDownside20Range: [number, number];
  volUpside20Range: [number, number];
  volRealizedRvRange: [number, number];
  // Technical
  volRatio5Range: number;
  volRatio20Range: number;
  mfi14Range: [number, number];
  bbPosRange: [number, number];
  adx14Range: [number, number];
  // Fund Flow
  mainFlowRange: [number, number];
  flowNetAmountRange: [number, number];
  flowLargeNetRange: [number, number];
  flowImbalanceRange: [number, number];
  flowMfiRange: [number, number];
  // Style
  beta20Range: [number, number];
  beta60Range: [number, number];
  idioVol20Range: [number, number];
  // Industry
  indStrength20Range: [number, number];
  indRet20Range: [number, number];
  indRelativeMomentum20Range: [number, number];
  // Chip
  chipProfitRatio20Range: [number, number];
  chipProfitRatio60Range: [number, number];
  chipFloatingRatioRange: [number, number];
  // Fundamental
  peRange: [number, number];
  roeRange: [number, number];
  profitGrowthRange: [number, number];
  pbRange: [number, number];
  psTtmRange: [number, number];
  instOwnershipRange: [number, number];
  listedDaysRange: [number, number];
  // Sector/Concept
  selectedSectors: string[];
  selectedConcepts: string[];
  selectedIndices: string[];
  marketType: string;
  // Meta
  volumeTrendOnly: boolean;
  advancedFiltersEnabled: boolean;
}

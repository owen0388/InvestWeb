export interface Paginated<T> {
  items: T[]
  page: number
  page_size: number
  total: number
  pages: number
}

export interface LatestQuote {
  close: number | null
  change_pct: number | null
  trade_date: string | null
  data_type: string | null
  observed_at: string | null
}

export interface Instrument {
  symbol: string
  name: string
  asset_class_code: string
  asset_class_name: string
  market: string
  currency: string
  instrument_type: string
  data_status: string
  data_status_label: string
  data_source_code: string | null
  analysis_module: string
  analysis_module_label: string
  risk_level: string
  liquidity_tier: string
  qualification_required: string
  tags: string[]
  notes: string
  enabled: boolean
  favorite: boolean
  last_data_at: string | null
  latest: LatestQuote
}

export interface RegimeDriver {
  key: string
  label: string
  value: number
  weight: number
  text: string | null
  as_of: string | null
}

export interface Regime {
  state: string
  score: number | null
  coverage: number
  min_coverage: number
  explanation: string
  drivers: RegimeDriver[]
  data_deficient: boolean
  as_of: string
}

export interface EventItem {
  id: number
  title: string
  summary: string
  category: string
  category_label: string
  published_at: string | null
  collected_at: string
  importance: number
  direction: string
  confidence: number
  data_quality: number
  source_count: number
  evidence_count: number
  horizon: string
  analysis_engine: string
  analysis_version: number
  what_happened: string
  what_uncertain: string
  what_changed: string
  invalidation: string
  countries: string[]
  industries: string[]
  watch_items: string[]
}

export interface OpportunityCard {
  type: string
  symbol: string
  name: string
  asset_class: string
  asset_class_code: string
  score: number | null
  confidence: number
  data_quality: number
  action: string
  direction: string
  as_of: string
  summary: string
  changes: number | null
  missing_inputs: string[]
  related_events: Array<{
    id: number
    title: string
    importance: number
    direction: string
    published_at: string | null
  }>
  decision_hint: string
}

export interface AllocationTargetRow {
  asset_class_code: string
  name: string
  base: number
  score: number | null
  confidence: number
  quality: number
  tilt: number
  min: number
  max: number
  reasons: string[]
  leaders: Array<{ symbol: string; score: number; action: string }>
  current: number
  target: number
  delta: number
  trade: boolean
  excluded: boolean
}

export interface AllocationResult {
  portfolio: {
    id: number
    name: string
    total_value: number
    is_example: boolean
    is_personalized: boolean
    base_source: string
    risk_tolerance: string
    horizon_years: number | null
    max_drawdown: number | null
  }
  as_of: string
  regime: string
  targets: AllocationTargetRow[]
  risk: {
    volatility: {
      current: { annual_vol: number; coverage: number; note: string } | null
      target: { annual_vol: number; coverage: number; note: string } | null
    }
    vol_change: number | null
    max_drawdown_estimate: number | null
    concentration: { max_class: { code: string; name: string; weight: number } | null; hhi: number }
    low_liquidity_weight: number
    correlation: { labels: string[]; values: number[][] }
    stress_tests: Array<{ name: string; impact: number; assumption: string }>
    notes: string[]
    data_limitations: string[]
  }
  turnover: number
  max_turnover: number
  warnings: string[]
  excluded_classes: string[]
  disclaimer: string
}

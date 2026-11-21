import { get, post, put, patch, del, upload } from './client'
import type { AllocationResult, EventItem, Instrument, OpportunityCard, Paginated, Regime } from '@/types'

export const api = {
  health: () => get<any>('/api/health'),
  systemInfo: () => get<any>('/api/system/info'),

  assetClasses: () => get<any>('/api/asset-classes'),
  instruments: (params: Record<string, unknown>) => get<Paginated<Instrument>>('/api/instruments', params),
  instrument: (symbol: string, days = 180) => get<any>(`/api/instruments/${encodeURIComponent(symbol)}`, { days }),
  updateInstrument: (symbol: string, body: Record<string, unknown>) =>
    patch<any>(`/api/instruments/${encodeURIComponent(symbol)}`, body),

  overview: () => get<any>('/api/market/overview'),
  regime: (refresh = false) => get<Regime>('/api/market/regime', { refresh }),
  regimeHistory: () => get<any>('/api/market/regime/history'),
  quotes: (params: Record<string, unknown> = {}) => get<any>('/api/market/quotes', params),

  macroIndicators: () => get<any>('/api/macro/indicators'),
  macroOverview: () => get<any>('/api/macro/overview'),
  macroSeries: (code: string, limit = 60) => get<any>(`/api/macro/series/${encodeURIComponent(code)}`, { limit }),

  news: (params: Record<string, unknown>) => get<Paginated<any>>('/api/news', params),
  newsSources: () => get<any>('/api/news/sources'),
  events: (params: Record<string, unknown>) => get<Paginated<EventItem>>('/api/events', params),
  eventCategories: () => get<any>('/api/events/categories'),
  event: (id: number) => get<any>(`/api/events/${id}`),
  opportunities: (params: Record<string, unknown>) => get<any>('/api/opportunities', params),

  portfolios: () => get<any>('/api/portfolios'),
  portfolio: (id: number) => get<any>(`/api/portfolios/${id}`),
  updatePortfolio: (id: number, body: Record<string, unknown>) => put<any>(`/api/portfolios/${id}`, body),
  addHolding: (id: number, body: Record<string, unknown>) => post<any>(`/api/portfolios/${id}/holdings`, body),
  updateHolding: (id: number, holdingId: number, body: Record<string, unknown>) =>
    put<any>(`/api/portfolios/${id}/holdings/${holdingId}`, body),
  deleteHolding: (id: number, holdingId: number) => del<any>(`/api/portfolios/${id}/holdings/${holdingId}`),
  importHoldings: (id: number, file: File) => upload<any>(`/api/portfolios/${id}/holdings/import`, file),
  profile: (id: number) => get<any>(`/api/portfolios/${id}/investor-profile`),
  updateProfile: (id: number, body: Record<string, unknown>) =>
    put<any>(`/api/portfolios/${id}/investor-profile`, body),
  constraints: (id: number) => get<any>(`/api/portfolios/${id}/constraints`),
  updateConstraints: (id: number, body: unknown) => put<any>(`/api/portfolios/${id}/constraints`, body),
  baseWeights: (id: number) => get<any>(`/api/portfolios/${id}/base-weights`),
  updateBaseWeights: (id: number, weights: Record<string, number>) =>
    put<any>(`/api/portfolios/${id}/base-weights`, { weights }),
  allocation: (id: number) => get<AllocationResult>(`/api/portfolios/${id}/allocation`),
  recomputeAllocation: (id: number) => post<AllocationResult>(`/api/portfolios/${id}/allocation/recompute`),
  allocationHistory: (id: number) => get<any>(`/api/portfolios/${id}/allocation/history`),

  backtests: () => get<any>('/api/backtests'),
  backtest: (id: number) => get<any>(`/api/backtests/${id}`),
  runBacktest: (body: Record<string, unknown>) => post<any>('/api/backtests', body),

  radarDaily: (limit = 10) => get<any>('/api/opportunity-radar/daily', { limit }),
  refreshStatus: () => get<any>('/api/refresh/status'),
  refreshData: (scope: string) => post<any>('/api/refresh', { scope }),
  eventEtfs: (eventId: number, limit = 8) => get<any>(`/api/events/${eventId}/etfs`, { limit }),
  compareStrategies: (body: Record<string, unknown> = {}) => post<any>('/api/backtests/comparison', body),
  journal: (params: Record<string, unknown> = {}) => get<any>('/api/journal', params),
  journalStats: () => get<any>('/api/journal/stats'),
  addJournal: (body: Record<string, unknown>) => post<any>('/api/journal', body),
  closeJournal: (id: number, body: Record<string, unknown>) => put<any>(`/api/journal/${id}/close`, body),
  deleteJournal: (id: number) => del<any>(`/api/journal/${id}`),

  universe: (params: Record<string, unknown> = {}) => get<any>('/api/universe', params),
  universeSummary: () => get<any>('/api/universe/summary'),
  universeDetail: (code: string) => get<any>(`/api/universe/${encodeURIComponent(code)}`),
  refreshUniverse: () => post<any>('/api/universe/refresh'),
  refreshQuality: () => post<any>('/api/universe/quality'),

  sources: () => get<any>('/api/sources'),  updateSource: (code: string, body: Record<string, unknown>) => patch<any>(`/api/sources/${code}`, body),
  sourceRuns: (code: string) => get<any>(`/api/sources/${code}/runs`),
  runSource: (code: string) => post<any>(`/api/sources/${code}/run`),

  jobs: () => get<any>('/api/jobs'),
  runJob: (id: string) => post<any>(`/api/jobs/${id}/run`),
  jobRuns: () => get<any>('/api/jobs/runs'),

  usage: () => get<any>('/api/usage'),
  notifications: () => get<any>('/api/notifications'),
  dispatchNotifications: () => post<any>('/api/notifications/dispatch'),
  digest: () => post<any>('/api/notifications/digest'),
  settings: () => get<any>('/api/settings'),
  updateSettings: (body: Record<string, unknown>) => put<any>('/api/settings', body),
}

import { reactive } from 'vue'
import { api } from '@/api/endpoints'
import type { Regime } from '@/types'

export const appState = reactive<{
  overview: any | null
  regime: Regime | null
  loading: boolean
  error: string
  loadedAt: string
}>({
  overview: null,
  regime: null,
  loading: false,
  error: '',
  loadedAt: '',
})

export async function loadOverview(force = false) {
  if (appState.loading) return
  if (!force && appState.overview && Date.now() - new Date(appState.loadedAt).getTime() < 60_000) return
  appState.loading = true
  appState.error = ''
  try {
    const data = await api.overview()
    appState.overview = data
    appState.regime = data.regime
    appState.loadedAt = new Date().toISOString()
  } catch (error: any) {
    appState.error = error?.message || '加载失败'
  } finally {
    appState.loading = false
  }
}

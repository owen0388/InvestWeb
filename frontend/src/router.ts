import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'overview', component: () => import('@/pages/OverviewPage.vue'), meta: { title: '市场总览' } },
  { path: '/radar', name: 'radar', component: () => import('@/pages/RadarPage.vue'), meta: { title: '机会雷达' } },
  { path: '/journal', name: 'journal', component: () => import('@/pages/JournalPage.vue'), meta: { title: '交易日志' } },
  { path: '/opportunities', name: 'opportunities', component: () => import('@/pages/OpportunitiesPage.vue'), meta: { title: '资产评分' } },
  { path: '/events', name: 'events', component: () => import('@/pages/EventsPage.vue'), meta: { title: '新闻与事件' } },
  { path: '/assets', name: 'assets', component: () => import('@/pages/AssetsPage.vue'), meta: { title: '资产目录' } },
  { path: '/universe', name: 'universe', component: () => import('@/pages/UniversePage.vue'), meta: { title: 'ETF 候选池' } },
  { path: '/assets/:symbol', name: 'asset-detail', component: () => import('@/pages/AssetDetailPage.vue'), meta: { title: '单品分析' } },
  { path: '/allocation', name: 'allocation', component: () => import('@/pages/AllocationPage.vue'), meta: { title: '资产配置' } },
  { path: '/macro', name: 'macro', component: () => import('@/pages/MacroPage.vue'), meta: { title: '宏观与风险' } },
  { path: '/backtest', name: 'backtest', component: () => import('@/pages/BacktestPage.vue'), meta: { title: '策略回测' } },
  { path: '/sources', name: 'sources', component: () => import('@/pages/SourcesPage.vue'), meta: { title: '数据源管理' } },
  { path: '/settings', name: 'settings', component: () => import('@/pages/SettingsPage.vue'), meta: { title: '系统设置' } },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: () => ({ top: 0 }),
})

router.afterEach((to) => {
  document.title = `${to.meta.title || '工作台'} · 投资情报工作台`
})

export default router

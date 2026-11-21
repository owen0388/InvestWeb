<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { appState, loadOverview } from '@/stores/app'
import { regimeClass, fmtNum } from '@/utils/format'

const route = useRoute()
// 小屏导航抽屉：12 个菜单平铺会占掉三分之一屏幕，改为点击展开
const navOpen = ref(false)
watch(() => route.path, () => { navOpen.value = false })
const nav = [
  { group: '研究', items: [
    { path: '/', label: '市场总览' },
    { path: '/radar', label: '机会雷达' },
    { path: '/events', label: '新闻与事件' },
  ]},
  { group: '资产', items: [
    { path: '/assets', label: '全品类目录' },
    { path: '/universe', label: 'ETF 候选池' },
    { path: '/opportunities', label: '资产评分' },
    { path: '/macro', label: '宏观与风险' },
  ]},
  { group: '决策', items: [
    { path: '/allocation', label: '资产配置' },
    { path: '/journal', label: '交易日志' },
    { path: '/backtest', label: '策略回测' },
  ]},
  { group: '系统', items: [
    { path: '/sources', label: '数据源管理' },
    { path: '/settings', label: '系统设置' },
  ]},
]

const regime = computed(() => appState.regime)
const activePath = computed(() => {
  if (route.path.startsWith('/assets/')) return '/assets'
  return route.path
})
const title = computed(() => (route.meta.title as string) || '市场总览')

onMounted(() => loadOverview())
</script>

<template>
  <div class="app-shell">
    <div v-if="navOpen" class="nav-mask" @click="navOpen = false" />
    <aside class="sidebar" :class="{ open: navOpen }">
      <div class="brand">
        <div class="brand-title">投资情报工作台</div>
        <div class="brand-sub">全品类 · 事件驱动 · 风险约束</div>
      </div>
      <nav class="nav">
        <template v-for="group in nav" :key="group.group">
          <div class="nav-group">{{ group.group }}</div>
          <router-link
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            class="nav-item"
            :class="{ active: activePath === item.path }"
          >
            <span class="nav-dot" />{{ item.label }}
          </router-link>
        </template>
      </nav>
      <div class="sidebar-foot">
        <div>仅研究与模拟</div>
        <div>不执行真实交易</div>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="topbar-left">
          <button class="nav-toggle" aria-label="菜单" @click="navOpen = true">☰</button>
          <h1>{{ title }}</h1>
          <span class="crumb">研究与模拟环境</span>
        </div>
        <div class="topbar-right">
          <span v-if="regime" class="tag" :class="regimeClass(regime.state)">
            <span class="regime-label">市场状态：</span>{{ regime.state }}
            <template v-if="regime.score !== null">· {{ fmtNum(regime.score, 1) }}</template>
          </span>
          <span v-else class="tag tag-muted">市场状态加载中</span>
          <button class="btn sm" :disabled="appState.loading" @click="loadOverview(true)">
            <span v-if="appState.loading" class="spin" />
            {{ appState.loading ? '刷新中' : '刷新' }}
          </button>
        </div>
      </header>
      <main class="page">
        <router-view />
      </main>
    </div>
  </div>
</template>

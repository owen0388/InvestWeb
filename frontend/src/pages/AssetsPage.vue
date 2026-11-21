<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '@/api/endpoints'
import type { Instrument } from '@/types'
import { changeClass, fmtNum, fmtPctPoint, relativeTime, statusLabel } from '@/utils/format'

const classes = ref<any[]>([])
const items = ref<Instrument[]>([])
const meta = ref<any>({ total: 0, pages: 1 })
const loading = ref(true)
const error = ref('')
const page = ref(1)
const filters = ref({ asset_class: '', q: '', status: '', market: '', favorite: false })
const classCounts = computed(() => {
  const total = classes.value.reduce((sum, c) => sum + (c.counts?.total || 0), 0)
  return total
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.instruments({
      asset_class: filters.value.asset_class,
      q: filters.value.q,
      status: filters.value.status,
      market: filters.value.market,
      favorite: filters.value.favorite || undefined,
      page: page.value,
      page_size: 24,
    })
    items.value = res.items
    meta.value = res
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function toggle(item: Instrument, field: 'enabled' | 'favorite') {
  try {
    const res = await api.updateInstrument(item.symbol, { [field]: !item[field] })
    Object.assign(item, res.instrument)
  } catch (err: any) {
    error.value = err?.message || '更新失败'
  }
}

onMounted(async () => {
  try {
    const res = await api.assetClasses()
    classes.value = res.items
  } catch (err: any) {
    error.value = err?.message || '资产目录加载失败'
  }
  await load()
})

watch(() => [filters.value.asset_class, filters.value.status, filters.value.market, filters.value.favorite], () => {
  page.value = 1
  load()
})
watch(page, load)
</script>

<template>
  <div class="section-head">
    <div>
      <h2>全品类资产目录</h2>
      <div class="section-desc">
        一级类别 {{ classes.length }} 类、登记品种 {{ classCounts }} 个。目录完整性不代表已接入实时数据，请查看每行的数据状态。
      </div>
    </div>
    <div class="pill-row">
      <span class="tag tag-down">正常</span>
      <span class="tag tag-warn">延迟/待核验</span>
      <span class="tag tag-danger">缺失</span>
      <span class="tag tag-neutral">人工维护</span>
      <span class="tag tag-muted">受限</span>
    </div>
  </div>

  <div class="grid" style="grid-template-columns: 236px minmax(0, 1fr); align-items: start">
    <aside class="card" style="position: sticky; top: 68px">
      <div class="card-head"><div class="card-title">资产类别</div></div>
      <div class="card-body tight">
        <div class="nav-like">
          <button class="class-item" :class="{ active: filters.asset_class === '' }" @click="filters.asset_class = ''">
            <span>全部类别</span><span class="num tiny muted">{{ classCounts }}</span>
          </button>
          <button
            v-for="c in classes" :key="c.code" class="class-item"
            :class="{ active: filters.asset_class === c.code }" @click="filters.asset_class = c.code"
          >
            <span class="wrap">{{ c.name }}</span>
            <span class="num tiny muted">{{ c.counts?.total || 0 }}</span>
          </button>
        </div>
      </div>
    </aside>

    <div>
      <div class="card" style="margin-bottom: 14px">
        <div class="card-body tight">
          <div class="toolbar">
            <input v-model="filters.q" class="input wide" placeholder="搜索代码或名称" @keyup.enter="page = 1; load()" />
            <select v-model="filters.status" class="select">
              <option value="">全部数据状态</option>
              <option value="normal">正常</option>
              <option value="delayed">延迟</option>
              <option value="missing">缺失</option>
              <option value="manual">人工维护</option>
              <option value="restricted">受限</option>
              <option value="unverified">待核验</option>
            </select>
            <select v-model="filters.market" class="select">
              <option value="">全部市场</option>
              <option value="CN">中国内地</option>
              <option value="HK">中国香港</option>
              <option value="US">美国</option>
              <option value="GLOBAL">全球</option>
            </select>
            <label class="small muted" style="display: inline-flex; align-items: center; gap: 4px">
              <input v-model="filters.favorite" type="checkbox" /> 仅收藏
            </label>
            <div class="spacer" />
            <button class="btn sm" @click="page = 1; load()">查询</button>
          </div>
        </div>
      </div>

      <div v-if="error" class="error-box" style="margin-bottom: 12px">{{ error }}</div>

      <div class="card">
        <div v-if="loading" class="loading"><span class="spin" /> 加载资产目录…</div>
        <div v-else class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>代码 / 名称</th><th>类别</th><th class="right">最新价</th><th class="right">涨跌幅</th>
                <th>数据状态</th><th>数据源</th><th>最后更新</th><th>分析模型</th><th class="right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="item.symbol">
                <td>
                  <router-link :to="`/assets/${encodeURIComponent(item.symbol)}`">{{ item.name }}</router-link>
                  <div class="sym">{{ item.symbol }} · {{ item.market }}</div>
                </td>
                <td class="small">{{ item.asset_class_name }}</td>
                <td class="right num">{{ item.latest.close !== null ? fmtNum(item.latest.close, item.latest.close > 1000 ? 1 : 3) : '—' }}</td>
                <td class="right num" :class="changeClass(item.latest.change_pct)">
                  {{ item.latest.change_pct !== null ? fmtPctPoint(item.latest.change_pct) : '—' }}
                </td>
                <td><span class="tag" :class="statusLabel(item.data_status).cls">{{ statusLabel(item.data_status).label }}</span></td>
                <td class="small muted">{{ item.data_source_code || '—' }}</td>
                <td class="small muted nowrap">{{ item.last_data_at ? relativeTime(item.last_data_at) : '从未采集' }}</td>
                <td class="small muted">{{ item.analysis_module_label ? item.analysis_module_label.split('：')[0] : '通用模型' }}</td>
                <td class="right nowrap">
                  <button class="btn sm ghost" @click="toggle(item, 'favorite')">{{ item.favorite ? '★' : '☆' }}</button>
                  <button class="btn sm ghost" @click="toggle(item, 'enabled')">{{ item.enabled ? '停用' : '启用' }}</button>
                </td>
              </tr>
              <tr v-if="!items.length"><td colspan="9" class="empty">没有匹配的品种</td></tr>
            </tbody>
          </table>
        </div>
        <div v-if="meta.pages > 1" class="card-foot toolbar" style="justify-content: center">
          <button class="btn sm" :disabled="page <= 1" @click="page -= 1">上一页</button>
          <span class="small muted">第 {{ page }} / {{ meta.pages }} 页 · 共 {{ meta.total }} 个</span>
          <button class="btn sm" :disabled="page >= meta.pages" @click="page += 1">下一页</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.class-item {
  width: 100%; display: flex; justify-content: space-between; gap: 6px; align-items: center;
  padding: 6px 8px; border: none; background: transparent; border-radius: 4px;
  font-size: 12px; color: var(--text-2); cursor: pointer; text-align: left; font-family: inherit;
}
.class-item:hover { background: #f3f7fc; }
.class-item.active { background: var(--primary-soft); color: var(--primary); font-weight: 500; }
</style>

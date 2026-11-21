<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '@/api/endpoints'
import SectionCard from '@/components/SectionCard.vue'
import type { OpportunityCard } from '@/types'
import { actionClass, directionClass, directionLabel, fmtDate, fmtNum, fmtPctPoint } from '@/utils/format'

const items = ref<OpportunityCard[]>([])
const meta = ref<any>({ total: 0, pages: 1, as_of: null, filters: { asset_classes: [], actions: [] } })
const loading = ref(true)
const error = ref('')
const page = ref(1)
const filters = ref({ asset_class: '', action: '', min_confidence: 0, direction: '' })

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.opportunities({
      asset_class: filters.value.asset_class,
      action: filters.value.action,
      min_confidence: filters.value.min_confidence || undefined,
      direction: filters.value.direction,
      page: page.value,
      page_size: 12,
    })
    items.value = res.items
    meta.value = res
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => filters.value, () => { page.value = 1; load() }, { deep: true })
watch(page, load)

function scoreWidth(score: number | null) {
  return `${Math.max(0, Math.min(100, score ?? 0))}%`
}
function scoreColor(score: number | null) {
  if (score === null) return '#c9d6e6'
  if (score >= 62) return '#d0342c'
  if (score <= 38) return '#0f8f63'
  return '#1a4f8b'
}
const asOfText = computed(() => (meta.value.as_of ? fmtDate(meta.value.as_of) : '尚未生成评分'))
</script>

<template>
  <div class="section-head">
    <div>
      <h2>全市场机会雷达</h2>
      <div class="section-desc">
        按评分变化与证据强度排序，评分日 {{ asOfText }}。仅展示有足够数据的品种，缺失数据不参与打分。
      </div>
    </div>
    <div class="tag tag-neutral">共 {{ meta.total }} 个可研究标的</div>
  </div>

  <div class="card" style="margin-bottom: 14px">
    <div class="card-body tight">
      <div class="toolbar">
        <select v-model="filters.asset_class" class="select">
          <option value="">全部资产类别</option>
          <option v-for="c in meta.filters?.asset_classes || []" :key="c.code" :value="c.code">{{ c.name }}</option>
        </select>
        <select v-model="filters.action" class="select">
          <option value="">全部建议动作</option>
          <option v-for="a in meta.filters?.actions || []" :key="a" :value="a">{{ a }}</option>
        </select>
        <select v-model="filters.direction" class="select">
          <option value="">全部方向线索</option>
          <option value="bullish">偏多事件关联</option>
          <option value="bearish">偏空事件关联</option>
          <option value="mixed">方向冲突</option>
        </select>
        <select v-model.number="filters.min_confidence" class="select">
          <option :value="0">全部置信度</option>
          <option :value="0.35">置信度 ≥ 35%</option>
          <option :value="0.5">置信度 ≥ 50%</option>
          <option :value="0.65">置信度 ≥ 65%</option>
        </select>
        <div class="spacer" />
        <button class="btn sm" @click="load">刷新</button>
      </div>
    </div>
  </div>

  <div v-if="loading" class="loading"><span class="spin" /> 正在计算机会列表…</div>
  <div v-else-if="error" class="error-box">{{ error }}</div>
  <div v-else-if="!items.length" class="card empty">
    当前筛选条件下没有结果。若尚未采集行情，请先在「数据源管理」触发一次行情与历史数据采集。
  </div>

  <div v-else class="grid g3">
    <article v-for="item in items" :key="item.symbol" class="card" style="padding: 14px">
      <div style="display: flex; justify-content: space-between; gap: 8px; align-items: flex-start">
        <div>
          <router-link :to="`/assets/${encodeURIComponent(item.symbol)}`" class="strong" style="font-size: 14px">
            {{ item.name }}
          </router-link>
          <div class="tiny muted">{{ item.symbol }} · {{ item.asset_class }}</div>
        </div>
        <span class="tag" :class="actionClass(item.action)">{{ item.action }}</span>
      </div>

      <div style="display: flex; align-items: baseline; gap: 8px; margin: 12px 0 4px">
        <span class="num" style="font-size: 26px; font-weight: 600">{{ fmtNum(item.score, 1) }}</span>
        <span class="tiny muted">综合相对吸引力 / 100</span>
        <span v-if="item.changes !== null" class="tiny num" :class="item.changes >= 0 ? 'up' : 'down'">
          较上期 {{ fmtPctPoint(item.changes, 1) }}
        </span>
      </div>
      <div class="bar"><span :style="{ width: scoreWidth(item.score), background: scoreColor(item.score) }" /></div>

      <p class="small" style="margin: 10px 0; color: var(--text-2)">{{ item.summary }}</p>

      <div class="pill-row" style="margin-bottom: 8px">
        <span class="tag tag-neutral">置信度 {{ (item.confidence * 100).toFixed(0) }}%</span>
        <span class="tag tag-neutral">数据质量 {{ (item.data_quality * 100).toFixed(0) }}%</span>
        <span class="tag" :class="directionClass(item.direction)">{{ directionLabel(item.direction) }}</span>
      </div>

      <div v-if="item.missing_inputs?.length" class="tiny muted" style="margin-bottom: 8px">
        缺失：{{ item.missing_inputs.slice(0, 2).join('；') }}
      </div>

      <div v-if="item.related_events?.length" style="border-top: 1px solid var(--line); padding-top: 8px">
        <div class="tiny muted" style="margin-bottom: 4px">关联事件</div>
        <div v-for="e in item.related_events" :key="e.id" class="small" style="margin-bottom: 2px">
          <router-link :to="`/events?id=${e.id}`">{{ e.title }}</router-link>
          <span class="tiny muted"> · 重要性 {{ e.importance }} · {{ directionLabel(e.direction) }}</span>
        </div>
      </div>
      <div v-else class="tiny muted" style="border-top: 1px solid var(--line); padding-top: 8px">
        近 30 日无直接相关事件，评分为趋势/宏观主导
      </div>
    </article>
  </div>

  <div v-if="meta.pages > 1" class="toolbar" style="justify-content: center; margin-top: 16px">
    <button class="btn sm" :disabled="page <= 1" @click="page -= 1">上一页</button>
    <span class="small muted">第 {{ page }} / {{ meta.pages }} 页</span>
    <button class="btn sm" :disabled="page >= meta.pages" @click="page += 1">下一页</button>
  </div>

  <div class="banner" style="margin-top: 16px">
    建议动作是研究流程提示，不是交易指令。若某个品种的关键数据缺失或过时，系统会降低置信度并禁止仅凭不可靠数据提高风险敞口。
  </div>
</template>

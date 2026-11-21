<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/endpoints'
import SectionCard from '@/components/SectionCard.vue'
import EChart from '@/components/EChart.vue'
import { fmtDate, fmtNum } from '@/utils/format'

const cards = ref<any[]>([])
const loading = ref(true)
const error = ref('')
const selected = ref<string>('')
const series = ref<any>(null)
const seriesLoading = ref(false)
const country = ref<'ALL' | 'CN' | 'US' | 'WLD'>('ALL')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.macroOverview()
    cards.value = res.items
    if (res.items.length) {
      const preferred = res.items.find((c: any) => c.code.includes('GDP') && c.country === 'CN') || res.items[0]
      select(preferred.code)
    }
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function select(code: string) {
  selected.value = code
  seriesLoading.value = true
  try {
    series.value = await api.macroSeries(code, 60)
  } catch (err: any) {
    series.value = null
  } finally {
    seriesLoading.value = false
  }
}

onMounted(load)

const filtered = computed(() => cards.value.filter((c) => country.value === 'ALL' || c.country === country.value))

function sparkOption(card: any) {
  const points = card.series.filter((p: any) => p.value !== null)
  return {
    grid: { left: 2, right: 2, top: 6, bottom: 2 },
    xAxis: { type: 'category', data: points.map((p: any) => p.period), show: false },
    yAxis: { type: 'value', scale: true, show: false },
    tooltip: { trigger: 'axis' },
    series: [{
      type: 'line', data: points.map((p: any) => p.value), showSymbol: false, smooth: false,
      lineStyle: { width: 1.4, color: card.staleness < 0.5 ? '#b0741a' : '#1a4f8b' },
      areaStyle: { color: card.staleness < 0.5 ? 'rgba(176,116,26,0.08)' : 'rgba(26,79,139,0.07)' },
    }],
  }
}

const detailOption = computed(() => {
  if (!series.value) return {}
  const pts = series.value.series.filter((p: any) => p.value !== null)
  return {
    grid: { left: 8, right: 14, top: 20, bottom: 6, containLabel: true },
    xAxis: { type: 'category', data: pts.map((p: any) => p.period), axisLabel: { color: '#8895a7', fontSize: 10 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e3e8f0' } } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: '#8895a7', fontSize: 10 }, splitLine: { lineStyle: { color: '#eef1f6' } } },
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => `${v}${series.value.unit || ''}` },
    series: [{
      type: 'line', data: pts.map((p: any) => p.value), showSymbol: true, symbolSize: 4,
      lineStyle: { width: 1.6, color: '#1a4f8b' },
      areaStyle: { color: 'rgba(26,79,139,0.06)' },
      markPoint: { data: [{ type: 'max', name: '最高' }, { type: 'min', name: '最低' }], label: { fontSize: 10 }, symbolSize: 34 },
    }],
  }
})

const currentCard = computed(() => cards.value.find((c) => c.code === selected.value))
</script>

<template>
  <div class="section-head">
    <div>
      <h2>宏观与风险</h2>
      <div class="section-desc">
        宏观指标严格区分「数据所属期间」与「系统采集时间」；年度数据不会标注为实时，并会按陈旧度降低权重。
      </div>
    </div>
    <div class="toolbar">
      <select v-model="country" class="select">
        <option value="ALL">全部经济体</option>
        <option value="CN">中国</option>
        <option value="US">美国</option>
        <option value="WLD">全球</option>
      </select>
      <button class="btn sm" @click="load">刷新</button>
    </div>
  </div>

  <div v-if="loading" class="loading"><span class="spin" /> 加载宏观指标…</div>
  <div v-else-if="error" class="error-box">{{ error }}</div>
  <div v-else-if="!cards.length" class="card empty">
    尚未采集宏观数据。请到「数据源管理」触发宏观刷新（世界银行免密钥；FRED 需配置 Key）。
  </div>

  <template v-else>
    <div class="grid g4" style="margin-bottom: 14px">
      <button
        v-for="card in filtered" :key="card.code" class="card macro-card"
        :class="{ active: selected === card.code }" @click="select(card.code)"
      >
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 6px">
          <div style="text-align: left">
            <div class="tiny muted">{{ card.name }}</div>
            <div class="num strong" style="font-size: 19px">
              {{ fmtNum(card.value, 2) }}<span class="tiny muted">{{ card.unit }}</span>
            </div>
            <div class="tiny muted">{{ card.period }} · {{ card.source }}</div>
          </div>
          <span class="tag" :class="card.staleness < 0.5 ? 'tag-warn' : 'tag-down'">
            {{ card.staleness < 0.5 ? '数据较旧' : '在有效期内' }}
          </span>
        </div>
        <EChart :option="sparkOption(card)" height="52px" />
      </button>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1.6fr) minmax(280px, 1fr)">
      <SectionCard :title="currentCard ? currentCard.name + ' 历史走势' : '历史走势'"
        :desc="currentCard ? `来源 ${currentCard.source} · 采集 ${currentCard.fetched_at} · 期间为年度` : ''">
        <div v-if="seriesLoading" class="loading"><span class="spin" /> 加载序列…</div>
        <EChart v-else-if="series" :option="detailOption" height="320px" />
        <div v-else class="empty">无序列数据</div>
        <template #footer>
          指标的 period 是数据所属期间，不是发布日；系统不会把上一年度 GDP 当作实时数据参与短线判断。
        </template>
      </SectionCard>

      <div class="grid" style="gap: 14px">
        <SectionCard title="指标明细">
          <dl v-if="currentCard" class="kv">
            <dt>指标代码</dt><dd class="wrap">{{ currentCard.code }}</dd>
            <dt>最新值</dt><dd>{{ fmtNum(currentCard.value, 2) }} {{ currentCard.unit }}</dd>
            <dt>上一值</dt><dd>{{ currentCard.previous_value !== null ? fmtNum(currentCard.previous_value, 2) : '—' }}</dd>
            <dt>所属期间</dt><dd>{{ currentCard.period }}</dd>
            <dt>采集时间</dt><dd>{{ currentCard.fetched_at }}</dd>
            <dt>陈旧系数</dt><dd>{{ (currentCard.staleness * 100).toFixed(0) }}%</dd>
            <dt>来源</dt><dd class="wrap">{{ currentCard.source }}</dd>
          </dl>
        </SectionCard>
        <SectionCard title="如何影响评分">
          <p class="small" style="color: var(--text-2)">
            宏观特征按资产模块映射：股票/商品看增长，债券/REITs/现金看通胀（反向），黄金看通胀（正向）。
            数据陈旧时特征值向中性 50 收缩，并降低该特征的 quality，进而降低整体置信度。
          </p>
          <p class="small muted" style="margin-top: 8px">
            国内外利率、信用利差等日频宏观数据需配置 FRED Key 后接入；未接入前不会被估算或虚构。
          </p>
        </SectionCard>
      </div>
    </div>
  </template>
</template>

<style scoped>
.macro-card {
  padding: 12px; text-align: left; cursor: pointer; font-family: inherit; color: inherit;
  transition: border-color 0.12s, box-shadow 0.12s;
}
.macro-card:hover { border-color: var(--line-strong); }
.macro-card.active { border-color: var(--primary); box-shadow: 0 0 0 2px rgba(26, 79, 139, 0.08); }
</style>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '@/api/endpoints'
import SectionCard from '@/components/SectionCard.vue'
import EChart from '@/components/EChart.vue'
import {
  actionClass, changeClass, directionClass, directionLabel, fmtDate, fmtNum, fmtPctPoint, importanceClass, relativeTime, statusLabel,
} from '@/utils/format'

const route = useRoute()
const router = useRouter()
const data = ref<any>(null)
const loading = ref(true)
const error = ref('')
const days = ref(180)
const manualValue = ref<number | null>(null)
const saving = ref(false)
const message = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.instrument(String(route.params.symbol), days.value)
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => route.params.symbol, load)
watch(days, load)

const history = computed<any[]>(() => data.value?.history || [])
const closes = computed(() => history.value.map((h) => h.close))
const stats = computed(() => {
  const values = closes.value
  if (values.length < 20) return { ret20: null, vol: null, dd: null, ma20: null, ma60: null }
  const ret20 = values[values.length - 1] / values[values.length - 21] - 1
  const rets: number[] = []
  for (let i = 1; i < values.length; i += 1) rets.push(values[i] / values[i - 1] - 1)
  const mean = rets.reduce((a, b) => a + b, 0) / rets.length
  const variance = rets.reduce((a, b) => a + (b - mean) ** 2, 0) / Math.max(1, rets.length - 1)
  const vol = Math.sqrt(variance) * Math.sqrt(252)
  let peak = values[0]
  let dd = 0
  values.forEach((v) => { peak = Math.max(peak, v); dd = Math.min(dd, v / peak - 1) })
  const ma = (n: number) => (values.length >= n ? values.slice(-n).reduce((a, b) => a + b, 0) / n : null)
  return { ret20, vol, dd, ma20: ma(20), ma60: ma(60) }
})

const priceOption = computed(() => {
  const dates = history.value.map((h) => h.date)
  const values = closes.value
  const ma = (n: number) => values.map((_, i) => {
    if (i < n - 1) return null
    const slice = values.slice(i - n + 1, i + 1)
    return Number((slice.reduce((a, b) => a + b, 0) / n).toFixed(3))
  })
  return {
    legend: { data: ['收盘价', 'MA20', 'MA60'], right: 0, top: 0, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: '#67748a' } },
    grid: { left: 8, right: 12, top: 30, bottom: 6, containLabel: true },
    xAxis: { type: 'category', data: dates, boundaryGap: false, axisLabel: { color: '#8895a7', fontSize: 10 }, axisLine: { lineStyle: { color: '#e3e8f0' } } },
    yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { color: '#eef1f6' } }, axisLabel: { color: '#8895a7', fontSize: 10 } },
    series: [
      { name: '收盘价', type: 'line', data: values, showSymbol: false, lineStyle: { width: 1.5, color: '#1a4f8b' }, areaStyle: { color: 'rgba(26,79,139,0.06)' } },
      { name: 'MA20', type: 'line', data: ma(20), showSymbol: false, lineStyle: { width: 1, color: '#c08a2e' } },
      { name: 'MA60', type: 'line', data: ma(60), showSymbol: false, lineStyle: { width: 1, color: '#7e8ea3' } },
    ],
  }
})

const moduleOption = computed(() => {
  const components = data.value?.latest_signal?.rationale?.features || {}
  const keys = Object.keys(components)
  return {
    grid: { left: 6, right: 30, top: 6, bottom: 0, containLabel: true },
    xAxis: { type: 'value', min: 0, max: 100, splitLine: { lineStyle: { color: '#eef1f6' } }, axisLabel: { color: '#8895a7', fontSize: 10 } },
    yAxis: {
      type: 'category',
      data: keys.map((k) => ({ trend: '趋势', macro: '宏观', catalyst: '事件催化', liquidity: '流动性', risk: '风险(越高越安全)', valuation: '估值/收益率', fundamental: '基本面' } as any)[k] || k),
      axisTick: { show: false }, axisLine: { show: false }, axisLabel: { color: '#3d4a5c', fontSize: 11 },
    },
    tooltip: { trigger: 'axis', formatter: (params: any) => params.map((p: any) => `${p.name}: ${p.value}`).join('<br/>') },
    series: [{ type: 'bar', data: keys.map((k) => components[k].value), barWidth: 11, itemStyle: { color: '#1a4f8b', borderRadius: [0, 2, 2, 0] }, label: { show: true, position: 'right', color: '#67748a', fontSize: 11 } }],
  }
})

const signal = computed(() => data.value?.latest_signal)
const instrument = computed(() => data.value?.instrument)

async function saveManual() {
  if (!instrument.value || manualValue.value === null) return
  saving.value = true
  message.value = ''
  try {
    await api.updateInstrument(instrument.value.symbol, { manual_value: manualValue.value })
    message.value = '已保存人工估值，刷新后用于评分'
    await load()
  } catch (err: any) {
    message.value = err?.message || '保存失败'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-if="loading" class="loading"><span class="spin" /> 加载单品数据…</div>
  <div v-else-if="error" class="error-box">{{ error }} <button class="btn sm" style="margin-left: 8px" @click="load">重试</button></div>

  <template v-else-if="data">
    <div class="section-head">
      <div>
        <div class="toolbar" style="margin-bottom: 6px">
          <button class="btn sm ghost" @click="router.push('/assets')">← 返回目录</button>
          <span class="tag tag-neutral">{{ data.class?.parent_name || data.class?.name }}</span>
          <span class="tag tag-neutral">{{ data.class?.name }}</span>
          <span class="tag" :class="statusLabel(instrument.data_status).cls">{{ statusLabel(instrument.data_status).label }}</span>
          <span class="tag tag-muted">{{ instrument.analysis_module_label.split('：')[0] }}</span>
        </div>
        <h2 style="font-size: 18px">{{ instrument.name }} <span class="muted small num">{{ instrument.symbol }}</span></h2>
        <div class="section-desc">{{ data.class?.description }}</div>
      </div>
      <div class="toolbar">
        <select v-model.number="days" class="select">
          <option :value="60">近 3 个月</option>
          <option :value="180">近 9 个月</option>
          <option :value="360">近 1.5 年</option>
          <option :value="720">近 3 年</option>
        </select>
      </div>
    </div>

    <div class="grid g5" style="margin-bottom: 14px">
      <div class="card kpi">
        <div class="kpi-label">最新价 <span class="tiny muted">{{ instrument.latest.data_type === 'realtime' ? '实时' : instrument.latest.data_type === 'delayed' ? '延迟' : instrument.latest.data_type === 'manual' ? '人工' : '收盘' }}</span></div>
        <div class="kpi-value">{{ instrument.latest.close !== null ? fmtNum(instrument.latest.close, instrument.latest.close > 1000 ? 1 : 3) : '—' }}</div>
        <div class="kpi-sub">{{ instrument.latest.trade_date || '无行情' }}</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">当日涨跌</div>
        <div class="kpi-value" :class="changeClass(instrument.latest.change_pct)">
          {{ instrument.latest.change_pct !== null ? fmtPctPoint(instrument.latest.change_pct) : '—' }}
        </div>
        <div class="kpi-sub">数据源 {{ instrument.latest.source || instrument.data_source_code }}</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">近 20 日</div>
        <div class="kpi-value" :class="changeClass(stats.ret20)">{{ stats.ret20 !== null ? fmtPctPoint(stats.ret20 * 100, 1) : '—' }}</div>
        <div class="kpi-sub">年化波动 {{ stats.vol !== null ? (stats.vol * 100).toFixed(0) + '%' : '—' }}</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">区间最大回撤</div>
        <div class="kpi-value down">{{ stats.dd !== null ? (stats.dd * 100).toFixed(1) + '%' : '—' }}</div>
        <div class="kpi-sub">样本 {{ history.length }} 个交易日</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">综合评分 <span v-if="signal" class="tag" :class="actionClass(signal.action)">{{ signal.action }}</span></div>
        <div class="kpi-value">{{ signal ? fmtNum(signal.total_score, 1) : '—' }}</div>
        <div class="kpi-sub">
          <template v-if="signal">置信度 {{ (signal.confidence * 100).toFixed(0) }}% · 覆盖 {{ ((signal.rationale?.coverage || 0) * 100).toFixed(0) }}%</template>
          <template v-else>数据不足，未生成评分</template>
        </div>
      </div>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1.6fr) minmax(300px, 1fr); margin-bottom: 14px">
      <SectionCard title="价格与均线" :desc="`${data.history[0]?.date || ''} 至 ${data.history[data.history.length - 1]?.date || ''} · 前复权收盘价`">
        <EChart v-if="history.length" :option="priceOption" height="300px" />
        <div v-else class="empty">暂无历史行情。请到「数据源管理」触发日线采集（仅沪深品种支持）。</div>
      </SectionCard>

      <SectionCard title="评分构成" desc="缺失特征不猜值，权重按可用项归一化">
        <EChart v-if="Object.keys(signal?.rationale?.features || {}).length" :option="moduleOption" height="200px" />
        <div v-else class="empty">无评分数据</div>
        <p v-if="signal" class="small" style="margin-top: 8px; color: var(--text-2)">{{ signal.rationale?.summary }}</p>
        <div v-if="signal?.missing_inputs?.length" class="banner warn" style="margin-top: 8px; display: block">
          <div class="strong" style="font-size: 11px; margin-bottom: 2px">未纳入评分的项目</div>
          <div class="tiny">{{ signal.missing_inputs.join('；') }}</div>
        </div>
      </SectionCard>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); margin-bottom: 14px">
      <SectionCard title="相关事件">
        <ul v-if="data.events?.length" class="list-clean">
          <li v-for="e in data.events" :key="e.id">
            <div style="display: flex; justify-content: space-between; gap: 8px">
              <router-link :to="`/events?id=${e.id}`" class="wrap">{{ e.title }}</router-link>
              <span class="tag" :class="importanceClass(e.importance)">{{ e.importance }}</span>
            </div>
            <div class="tiny muted">
              {{ e.category }} · {{ directionLabel(e.direction) }} · {{ fmtDate(e.published_at, true) }}
            </div>
            <div class="tiny muted wrap">{{ e.mechanism }}</div>
          </li>
        </ul>
        <div v-else class="empty">暂无关联事件</div>
      </SectionCard>

      <SectionCard title="信号历史">
        <div class="table-wrap" style="max-height: 280px">
          <table class="table">
            <thead><tr><th>日期</th><th class="right">评分</th><th class="right">置信度</th><th>状态</th><th>动作</th></tr></thead>
            <tbody>
              <tr v-for="s in data.signals" :key="s.as_of">
                <td class="num">{{ s.as_of }}</td>
                <td class="right num">{{ fmtNum(s.total_score, 1) }}</td>
                <td class="right num">{{ (s.confidence * 100).toFixed(0) }}%</td>
                <td class="small muted">{{ s.regime }}</td>
                <td><span class="tag" :class="actionClass(s.action)">{{ s.action }}</span></td>
              </tr>
              <tr v-if="!data.signals?.length"><td colspan="5" class="empty">尚未生成信号</td></tr>
            </tbody>
          </table>
        </div>
      </SectionCard>
    </div>

    <SectionCard title="数据说明与人工维护">
      <div class="grid g2">
        <dl class="kv">
          <dt>数据源</dt><dd>{{ instrument.data_source_code || '未配置' }}</dd>
          <dt>最后采集</dt><dd>{{ instrument.last_data_at ? relativeTime(instrument.last_data_at) : '从未' }}</dd>
          <dt>数据状态</dt><dd>{{ statusLabel(instrument.data_status).label }}</dd>
          <dt>风险等级</dt><dd>{{ instrument.risk_level }}</dd>
          <dt>流动性</dt><dd>{{ instrument.liquidity_tier }}</dd>
          <dt>准入要求</dt><dd>{{ instrument.qualification_required }}</dd>
          <dt>权重区间</dt><dd>{{ data.class?.min_weight !== null ? (data.class.min_weight * 100).toFixed(0) + '% ~ ' + (data.class.max_weight * 100).toFixed(0) + '%' : '—' }}</dd>
        </dl>
        <div>
          <div v-if="instrument.notes" class="banner" style="margin-bottom: 10px">{{ instrument.notes }}</div>
          <div v-if="instrument.data_source_code === 'manual'" class="banner info" style="display: block">
            <div class="strong" style="font-size: 12px; margin-bottom: 6px">人工录入估值</div>
            <div class="toolbar">
              <input v-model.number="manualValue" type="number" step="0.0001" class="input" placeholder="输入当前估值/净值" />
              <button class="btn primary sm" :disabled="saving" @click="saveManual">保存</button>
            </div>
            <div class="tiny muted" style="margin-top: 6px">人工维护数据不参与自动信号，仅在明确标注下用于研究参考。</div>
          </div>
          <div v-if="message" class="tiny" style="margin-top: 8px">{{ message }}</div>
        </div>
      </div>
    </SectionCard>
  </template>
</template>

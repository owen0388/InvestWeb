<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/endpoints'
import SectionCard from '@/components/SectionCard.vue'
import EChart from '@/components/EChart.vue'
import { fmtDate, fmtNum, fmtPctPoint, statusLabel } from '@/utils/format'

const runs = ref<any[]>([])
const current = ref<any>(null)
const loading = ref(true)
const running = ref(false)
const error = ref('')
const form = ref({ max_tilt: 0.05, transaction_cost_bps: 8, rebalance_days: 20 })
const currentId = ref<number | null>(null)

async function load() {
  loading.value = true
  try {
    const res = await api.backtests()
    runs.value = res.items
    const success = res.items.find((r: any) => r.has_curve)
    if (success) await open(success.id)
    else if (res.items.length) await open(res.items[0].id)
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function open(id: number) {
  currentId.value = id
  try {
    current.value = await api.backtest(id)
  } catch (err: any) {
    error.value = err?.message || '读取回测失败'
  }
}

async function run() {
  running.value = true
  error.value = ''
  try {
    const res = await api.runBacktest({ ...form.value })
    current.value = res
    currentId.value = res.id
    const list = await api.backtests()
    runs.value = list.items
  } catch (err: any) {
    error.value = typeof err?.detail === 'object' ? err.detail?.message || err.message : err?.message || '回测失败'
  } finally {
    running.value = false
  }
}

onMounted(load)

const curveOption = computed(() => {
  const curve = current.value?.equity_curve || []
  return {
    legend: { data: ['策略净值', '沪深300'], right: 0, top: 0, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: '#67748a' } },
    grid: { left: 8, right: 12, top: 28, bottom: 6, containLabel: true },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: curve.map((p: any) => p.date), axisLabel: { color: '#8895a7', fontSize: 10 }, axisLine: { lineStyle: { color: '#e3e8f0' } } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: '#8895a7', fontSize: 10 }, splitLine: { lineStyle: { color: '#eef1f6' } } },
    series: [
      { name: '策略净值', type: 'line', data: curve.map((p: any) => p.strategy), showSymbol: false, lineStyle: { width: 1.8, color: '#1a4f8b' }, areaStyle: { color: 'rgba(26,79,139,0.05)' } },
      { name: '沪深300', type: 'line', data: curve.map((p: any) => p.benchmark), showSymbol: false, lineStyle: { width: 1.2, color: '#9aa8ba', type: 'dashed' } },
    ],
  }
})

const metrics = computed(() => current.value?.metrics || {})
const rows = computed(() => [
  { label: '累计收益', value: metrics.value.total_return, pct: true },
  { label: '年化收益', value: metrics.value.annual_return, pct: true },
  { label: '年化波动', value: metrics.value.annual_vol, pct: true, plain: true },
  { label: '最大回撤', value: metrics.value.max_drawdown, pct: true, down: true },
  { label: '夏普（rf=1.5%）', value: metrics.value.sharpe, pct: false },
  { label: '换手合计', value: metrics.value.turnover_total, pct: false },
  { label: '成本合计', value: metrics.value.cost_total, pct: false },
  { label: '超额收益（对基准）', value: metrics.value.excess_return, pct: true },
])
</script>

<template>
  <div class="section-head">
    <div>
      <h2>策略回测</h2>
      <div class="section-desc">
        使用真实前复权日线，调仓日只使用当日之前数据；不含宏观与事件信号，以避免前视偏差与数据 vintage 问题。
      </div>
    </div>
  </div>

  <div class="card" style="margin-bottom: 14px">
    <div class="card-body tight">
      <div class="toolbar">
        <label class="small muted">单次最大倾斜
          <input v-model.number="form.max_tilt" type="number" step="0.01" class="input" style="width: 90px; margin-left: 4px" />
        </label>
        <label class="small muted">交易成本 (bps)
          <input v-model.number="form.transaction_cost_bps" type="number" class="input" style="width: 90px; margin-left: 4px" />
        </label>
        <label class="small muted">调仓周期（交易日）
          <input v-model.number="form.rebalance_days" type="number" class="input" style="width: 90px; margin-left: 4px" />
        </label>
        <button class="btn primary sm" :disabled="running" @click="run">
          <span v-if="running" class="spin" />{{ running ? '运行中' : '运行回测' }}
        </button>
        <div class="spacer" />
        <select v-model.number="currentId" class="select" @change="currentId && open(currentId)">
          <option v-for="r in runs" :key="r.id" :value="r.id">{{ fmtDate(r.created_at, true) }} · {{ statusLabel(r.status).label }}</option>
        </select>
      </div>
    </div>
  </div>

  <div v-if="error" class="error-box" style="margin-bottom: 12px">{{ error }}</div>
  <div v-if="loading" class="loading"><span class="spin" /> 加载回测记录…</div>

  <template v-else-if="current && current.status === 'success'">
    <div class="grid g4" style="margin-bottom: 14px">
      <div class="card kpi">
        <div class="kpi-label">累计收益</div>
        <div class="kpi-value" :class="(metrics.total_return || 0) >= 0 ? 'up' : 'down'">{{ fmtPctPoint((metrics.total_return || 0) * 100) }}</div>
        <div class="kpi-sub">{{ fmtDate(current.start_date) }} ~ {{ fmtDate(current.end_date) }}</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">年化 / 波动</div>
        <div class="kpi-value sm">{{ fmtPctPoint((metrics.annual_return || 0) * 100) }}</div>
        <div class="kpi-sub">波动 {{ ((metrics.annual_vol || 0) * 100).toFixed(2) }}%</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">最大回撤</div>
        <div class="kpi-value sm down">{{ fmtPctPoint((metrics.max_drawdown || 0) * 100) }}</div>
        <div class="kpi-sub">基准 {{ fmtPctPoint((metrics.benchmark?.max_drawdown || 0) * 100) }}</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">超额收益</div>
        <div class="kpi-value sm" :class="(metrics.excess_return || 0) >= 0 ? 'up' : 'down'">{{ fmtPctPoint((metrics.excess_return || 0) * 100) }}</div>
        <div class="kpi-sub">换手 {{ metrics.turnover_total }} · 成本 {{ (metrics.cost_total * 100).toFixed(2) }}%</div>
      </div>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1.7fr) minmax(300px, 1fr); margin-bottom: 14px">
      <SectionCard title="模拟净值 vs 沪深300" :desc="`${current.assumptions?.rebalance || ''} · 成本 ${current.assumptions?.transaction_cost_bps} bps`">
        <EChart :option="curveOption" height="330px" />
      </SectionCard>

      <SectionCard title="完整指标">
        <table class="table">
          <tbody>
            <tr v-for="row in rows" :key="row.label">
              <td class="muted">{{ row.label }}</td>
              <td class="right num strong">
                {{ row.pct ? (row.plain ? ((row.value || 0) * 100).toFixed(2) + '%' : fmtPctPoint((row.value || 0) * 100)) : fmtNum(row.value, 4) }}
              </td>
            </tr>
            <tr><td class="muted">样本内累计</td><td class="right num">{{ metrics.in_sample ? fmtPctPoint((metrics.in_sample.total_return || 0) * 100) : '—' }}</td></tr>
            <tr><td class="muted">样本外累计</td><td class="right num">{{ metrics.out_of_sample?.total_return !== undefined ? fmtPctPoint((metrics.out_of_sample.total_return || 0) * 100) : '—' }}</td></tr>
          </tbody>
        </table>
      </SectionCard>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); margin-bottom: 14px">
      <SectionCard title="调仓记录（最近）">
        <div class="table-wrap" style="max-height: 320px">
          <table class="table">
            <thead><tr><th>日期</th><th class="right">换手</th><th class="right">成本</th><th>权重</th></tr></thead>
            <tbody>
              <tr v-for="r in (current.rebalances || []).slice().reverse()" :key="r.date">
                <td class="num">{{ r.date }}</td>
                <td class="right num">{{ (r.turnover * 100).toFixed(1) }}%</td>
                <td class="right num">{{ (r.cost * 100).toFixed(3) }}%</td>
                <td class="tiny muted wrap">
                  <span v-for="(w, k) in r.weights" :key="k">{{ k }} {{ (w * 100).toFixed(0) }}% </span>
                </td>
              </tr>
              <tr v-if="!current.rebalances?.length"><td colspan="4" class="empty">无调仓记录</td></tr>
            </tbody>
          </table>
        </div>
      </SectionCard>

      <SectionCard title="假设与局限" desc="结果必须结合数据范围与假设解读">
        <dl class="kv" style="grid-template-columns: 100px 1fr">
          <dt>数据范围</dt><dd>{{ current.assumptions?.data_range }}</dd>
          <dt>价格</dt><dd>{{ current.assumptions?.price }}</dd>
          <dt>分红</dt><dd>{{ current.assumptions?.dividends }}</dd>
          <dt>信号</dt><dd>{{ current.assumptions?.scores }}</dd>
          <dt>基准</dt><dd>{{ current.assumptions?.benchmark }}</dd>
          <dt>成本</dt><dd>{{ current.assumptions?.transaction_cost_bps }} bps 单边</dd>
        </dl>
        <ul class="small" style="margin: 10px 0 0; padding-left: 16px; color: var(--text-2)">
          <li v-for="(w, i) in current.warnings" :key="i" style="margin-bottom: 4px">{{ w }}</li>
        </ul>
      </SectionCard>
    </div>

    <div class="banner warn">{{ current.data_note }}</div>
  </template>

  <div v-else-if="current" class="card" style="padding: 18px">
    <div class="strong" style="margin-bottom: 6px">回测未完成</div>
    <p class="small" style="color: var(--text-2)">{{ current.error || '数据不足，无法生成有效回测。' }}</p>
    <p class="tiny muted" style="margin-top: 6px">系统不会用伪造数据填充结果。请先完成日线历史采集后重试。</p>
  </div>

  <div v-else class="card empty">还没有回测记录，点击「运行回测」生成第一条。</div>
</template>

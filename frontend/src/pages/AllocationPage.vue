<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/endpoints'
import SectionCard from '@/components/SectionCard.vue'
import EChart from '@/components/EChart.vue'
import type { AllocationResult } from '@/types'
import { fmtMoney, fmtNum, fmtPctPoint } from '@/utils/format'

const tab = ref<'plan' | 'holdings' | 'risk'>('plan')
const allocation = ref<AllocationResult | null>(null)
const portfolio = ref<any>(null)
const baseWeights = ref<any>(null)
const loading = ref(true)
const error = ref('')
const message = ref('')
const busy = ref(false)
const portfolioId = ref<number>(1)
const portfolios = ref<any[]>([])

const profileForm = ref<any>({ risk_tolerance: 'balanced', horizon_years: 3, max_drawdown: 0.15, confirmed: false })
const holdingsForm = ref({ symbol: '', quantity: 0, cost_price: 0 })
const weightDraft = ref<Record<string, number>>({})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const list = await api.portfolios()
    portfolios.value = list.items
    if (list.items.length) {
      const preferred = list.items.find((p: any) => p.is_default) || list.items[0]
      portfolioId.value = preferred.id
    }
    await loadPortfolio()
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadPortfolio() {
  busy.value = true
  error.value = ''
  try {
    portfolio.value = await api.portfolio(portfolioId.value)
    allocation.value = await api.allocation(portfolioId.value)
    baseWeights.value = await api.baseWeights(portfolioId.value)
    weightDraft.value = { ...baseWeights.value.weights }
    profileForm.value = { ...(portfolio.value.profile || {}), confirmed: portfolio.value.profile?.confirmed ?? false }
  } catch (err: any) {
    error.value = err?.message || '组合数据加载失败'
  } finally {
    busy.value = false
  }
}

onMounted(load)

async function recompute() {
  busy.value = true
  message.value = ''
  try {
    allocation.value = await api.recomputeAllocation(portfolioId.value)
    message.value = '已生成新一版目标权重并写入历史记录'
  } catch (err: any) {
    message.value = err?.message || '计算失败'
  } finally {
    busy.value = false
  }
}

async function saveWeights() {
  const total = Object.values(weightDraft.value).reduce((a, b) => a + Number(b || 0), 0)
  if (Math.abs(total - 1) > 0.001) {
    message.value = `基础权重合计必须为 100%，当前 ${(total * 100).toFixed(1)}%`
    return
  }
  busy.value = true
  try {
    await api.updateBaseWeights(portfolioId.value, weightDraft.value)
    await loadPortfolio()
    message.value = '基础权重已保存；战术调整仍受约束投影限制'
  } catch (err: any) {
    message.value = err?.message || '保存失败'
  } finally {
    busy.value = false
  }
}

async function saveProfile() {
  busy.value = true
  try {
    await api.updateProfile(portfolioId.value, {
      risk_tolerance: profileForm.value.risk_tolerance,
      horizon_years: Number(profileForm.value.horizon_years),
      max_drawdown: Number(profileForm.value.max_drawdown),
      confirmed: profileForm.value.confirmed,
      notes: profileForm.value.notes,
    })
    await loadPortfolio()
    message.value = '投资者配置已更新'
  } catch (err: any) {
    message.value = err?.message || '保存失败'
  } finally {
    busy.value = false
  }
}

async function addHolding() {
  if (!holdingsForm.value.symbol) return
  busy.value = true
  try {
    await api.addHolding(portfolioId.value, { ...holdingsForm.value })
    holdingsForm.value = { symbol: '', quantity: 0, cost_price: 0 }
    await loadPortfolio()
    message.value = '持仓已更新'
  } catch (err: any) {
    message.value = err?.message || '添加失败'
  } finally {
    busy.value = false
  }
}

async function removeHolding(id: number) {
  busy.value = true
  try {
    await api.deleteHolding(portfolioId.value, id)
    await loadPortfolio()
  } catch (err: any) {
    message.value = err?.message || '删除失败'
  } finally {
    busy.value = false
  }
}

async function importCsv(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  busy.value = true
  try {
    const res = await api.importHoldings(portfolioId.value, file)
    message.value = `导入 ${res.imported} 条，跳过：${res.failed.join('、') || '无'}`
    await loadPortfolio()
  } catch (err: any) {
    message.value = err?.message || '导入失败'
  } finally {
    busy.value = false
  }
}

const targets = computed(() => allocation.value?.targets || [])
const risk = computed(() => allocation.value?.risk)

const weightOption = computed(() => {
  const rows = targets.value
  return {
    legend: { data: ['当前', '基础', '目标'], right: 0, top: 0, itemWidth: 12, itemHeight: 8, textStyle: { fontSize: 11, color: '#67748a' } },
    grid: { left: 8, right: 12, top: 30, bottom: 6, containLabel: true },
    tooltip: { trigger: 'axis', valueFormatter: (v: any) => `${(v * 100).toFixed(2)}%` },
    xAxis: { type: 'category', data: rows.map((r) => r.name.replace('与', '/').slice(0, 6)), axisLabel: { fontSize: 10, color: '#8895a7', interval: 0, rotate: 25 }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#e3e8f0' } } },
    yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%`, color: '#8895a7', fontSize: 10 }, splitLine: { lineStyle: { color: '#eef1f6' } } },
    series: [
      { name: '当前', type: 'bar', data: rows.map((r) => r.current), barWidth: 10, itemStyle: { color: '#c3cfdf' } },
      { name: '基础', type: 'bar', data: rows.map((r) => r.base), barWidth: 10, itemStyle: { color: '#8ba3c2' } },
      { name: '目标', type: 'bar', data: rows.map((r) => r.target), barWidth: 10, itemStyle: { color: '#1a4f8b' } },
    ],
  }
})

const pieOption = computed(() => ({
  tooltip: { trigger: 'item', formatter: '{b}: {d}%' },
  legend: { orient: 'vertical', right: 0, top: 'center', itemWidth: 10, itemHeight: 8, textStyle: { fontSize: 11, color: '#67748a' } },
  series: [{
    type: 'pie', radius: ['46%', '68%'], center: ['36%', '50%'], avoidLabelOverlap: true,
    itemStyle: { borderColor: '#fff', borderWidth: 1 },
    label: { show: false },
    data: targets.value.filter((t) => t.target > 0).map((t) => ({ name: t.name, value: Number((t.target * 100).toFixed(2)) })),
  }],
  color: ['#1a4f8b', '#3f74a8', '#6b93bd', '#9db6cf', '#c2d2e2', '#b08a4f', '#8a9aa9', '#d6c3a1'],
}))

function heatColor(value: number) {
  if (value >= 0.7) return '#c9443a'
  if (value >= 0.3) return '#d9a06a'
  if (value >= 0) return '#e8dfc9'
  if (value >= -0.3) return '#a8c8bb'
  return '#3f8b6f'
}
</script>

<template>
  <div v-if="loading" class="loading"><span class="spin" /> 加载组合与目标权重…</div>
  <div v-else-if="error" class="error-box">{{ error }}</div>

  <template v-else-if="allocation">
    <div class="section-head">
      <div>
        <h2>{{ allocation.portfolio.name }}</h2>
        <div class="section-desc">
          基础权重来源：{{ allocation.portfolio.base_source }} · 生成于 {{ allocation.as_of }} · 当前市场状态 {{ allocation.regime }}
        </div>
      </div>
      <div class="toolbar">
        <select v-model.number="portfolioId" class="select" @change="loadPortfolio">
          <option v-for="p in portfolios" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
        <button class="btn primary sm" :disabled="busy" @click="recompute">重新计算目标权重</button>
      </div>
    </div>

    <div class="banner" :class="allocation.portfolio.is_personalized ? 'info' : 'warn'" style="margin-bottom: 14px">
      {{ allocation.disclaimer }}
      <span v-if="!allocation.portfolio.is_personalized">（当前为示例组合，勿视为个性化建议）</span>
    </div>
    <div v-if="message" class="banner info" style="margin-bottom: 12px">{{ message }}</div>

    <div class="grid g4" style="margin-bottom: 14px">
      <div class="card kpi">
        <div class="kpi-label">组合规模</div>
        <div class="kpi-value sm">{{ fmtMoney(allocation.portfolio.total_value) }}</div>
        <div class="kpi-sub">现金 {{ fmtMoney(portfolio?.portfolio?.cash_value) }}</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">本次换手</div>
        <div class="kpi-value sm">{{ (allocation.turnover * 100).toFixed(1) }}%</div>
        <div class="kpi-sub">上限 {{ (allocation.max_turnover * 100).toFixed(0) }}%</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">目标组合波动（估算）</div>
        <div class="kpi-value sm">
          {{ risk?.volatility?.target ? (risk.volatility.target.annual_vol * 100).toFixed(1) + '%' : '不可计算' }}
        </div>
        <div class="kpi-sub">
          <template v-if="risk?.vol_change !== null && risk?.vol_change !== undefined">
            较当前 {{ fmtPctPoint(risk.vol_change * 100, 2) }}
          </template>
          <template v-else>历史序列不足</template>
        </div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">集中度（HHI）</div>
        <div class="kpi-value sm">{{ risk?.concentration?.hhi?.toFixed(3) ?? '—' }}</div>
        <div class="kpi-sub">最大类别 {{ risk?.concentration?.max_class?.name || '—' }} {{ risk?.concentration?.max_class ? (risk.concentration.max_class.weight * 100).toFixed(0) + '%' : '' }}</div>
      </div>
    </div>

    <div class="tabs" style="margin-bottom: 12px">
      <div class="tab" :class="{ active: tab === 'plan' }" @click="tab = 'plan'">配置方案</div>
      <div class="tab" :class="{ active: tab === 'holdings' }" @click="tab = 'holdings'">持仓与投资者配置</div>
      <div class="tab" :class="{ active: tab === 'risk' }" @click="tab = 'risk'">风险与压力测试</div>
    </div>

    <template v-if="tab === 'plan'">
      <div class="grid" style="grid-template-columns: minmax(0, 1.7fr) minmax(300px, 1fr); margin-bottom: 14px">
        <SectionCard title="当前 / 基础 / 目标权重" desc="战术倾斜受评分、置信度与上下限共同约束">
          <EChart :option="weightOption" height="280px" />
        </SectionCard>
        <SectionCard title="目标权重结构">
          <EChart v-if="targets.length" :option="pieOption" height="240px" />
          <div class="small muted" style="margin-top: 6px">
            权益、债券、黄金等类别的评分不可跨类别比较，权重差异仅代表相对研究倾斜。
          </div>
        </SectionCard>
      </div>

      <SectionCard title="目标权重明细">
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>资产类别</th><th class="right">当前</th><th class="right">基础</th><th class="right">战术倾斜</th>
                <th class="right">目标</th><th class="right">调整</th><th class="right">评分</th>
                <th class="right">置信度</th><th>约束区间</th><th>理由</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in targets" :key="t.asset_class_code">
                <td>{{ t.name }}<div class="sym">{{ t.asset_class_code }}</div></td>
                <td class="right num">{{ (t.current * 100).toFixed(2) }}%</td>
                <td class="right num">{{ (t.base * 100).toFixed(2) }}%</td>
                <td class="right num" :class="t.tilt > 0 ? 'up' : t.tilt < 0 ? 'down' : ''">
                  {{ t.tilt ? fmtPctPoint(t.tilt * 100, 2) : '—' }}
                </td>
                <td class="right num strong">{{ (t.target * 100).toFixed(2) }}%</td>
                <td class="right num" :class="t.delta > 0 ? 'up' : t.delta < 0 ? 'down' : ''">
                  {{ t.delta ? fmtPctPoint(t.delta * 100, 2) : '—' }}
                </td>
                <td class="right num">{{ t.score !== null ? fmtNum(t.score, 1) : '—' }}</td>
                <td class="right num">{{ t.confidence ? (t.confidence * 100).toFixed(0) + '%' : '—' }}</td>
                <td class="small muted num">{{ (t.min * 100).toFixed(0) }}% ~ {{ (t.max * 100).toFixed(0) }}%</td>
                <td class="small muted wrap" style="max-width: 280px">{{ t.reasons.join('；') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <template #footer>
          <span v-if="allocation.warnings.length">{{ allocation.warnings.join('；') }}</span>
          <span v-else>约束求解同时满足类别上下限、总和为 1、换手上限与最小交易阈值。</span>
        </template>
      </SectionCard>
    </template>

    <template v-else-if="tab === 'holdings'">
      <div class="grid" style="grid-template-columns: minmax(0, 1.4fr) minmax(320px, 1fr); margin-bottom: 14px">
        <SectionCard title="当前持仓">
          <div class="table-wrap">
            <table class="table">
              <thead><tr><th>品种</th><th class="right">数量</th><th class="right">市值</th><th class="right">权重</th><th class="right">最新价</th><th></th></tr></thead>
              <tbody>
                <tr v-for="h in portfolio?.holdings || []" :key="h.id">
                  <td>{{ h.name }}<div class="sym">{{ h.symbol }} · {{ h.class }}</div></td>
                  <td class="right num">{{ fmtNum(h.quantity, 0) }}</td>
                  <td class="right num">{{ fmtMoney(h.market_value) }}</td>
                  <td class="right num">{{ (h.weight * 100).toFixed(2) }}%</td>
                  <td class="right num">{{ h.latest_price !== null ? fmtNum(h.latest_price, 3) : '—' }}</td>
                  <td class="right"><button class="btn sm ghost" @click="removeHolding(h.id)">删除</button></td>
                </tr>
                <tr v-if="!portfolio?.holdings?.length"><td colspan="6" class="empty">暂无持仓</td></tr>
              </tbody>
            </table>
          </div>
          <template #footer>
            <div class="toolbar">
              <input v-model="holdingsForm.symbol" class="input" placeholder="代码，如 sh510300" />
              <input v-model.number="holdingsForm.quantity" type="number" class="input" placeholder="数量" style="width: 96px" />
              <input v-model.number="holdingsForm.cost_price" type="number" class="input" placeholder="成本价" style="width: 96px" />
              <button class="btn sm primary" :disabled="busy" @click="addHolding">添加/更新</button>
              <label class="btn sm" style="cursor: pointer">
                导入 CSV
                <input type="file" accept=".csv" hidden @change="importCsv" />
              </label>
            </div>
            <div class="tiny muted" style="margin-top: 6px">CSV 列：symbol,quantity,cost_price（也支持 代码/数量/成本价）</div>
          </template>
        </SectionCard>

        <SectionCard title="投资者配置" desc="未确认时仅展示示例组合，不构成个性化建议">
          <div class="grid" style="gap: 10px">
            <label class="small muted">风险偏好
              <select v-model="profileForm.risk_tolerance" class="select" style="width: 100%; margin-top: 3px">
                <option value="conservative">稳健</option>
                <option value="balanced">平衡</option>
                <option value="aggressive">进取</option>
              </select>
            </label>
            <div class="grid g2" style="gap: 10px">
              <label class="small muted">投资期限（年）
                <input v-model.number="profileForm.horizon_years" type="number" class="input" style="width: 100%; margin-top: 3px" />
              </label>
              <label class="small muted">最大可接受回撤
                <input v-model.number="profileForm.max_drawdown" type="number" step="0.01" class="input" style="width: 100%; margin-top: 3px" />
              </label>
            </div>
            <label class="small muted">备注
              <textarea v-model="profileForm.notes" class="input" style="width: 100%; height: 54px; padding: 6px; margin-top: 3px" />
            </label>
            <label class="small" style="display: flex; align-items: center; gap: 6px">
              <input v-model="profileForm.confirmed" type="checkbox" />
              我已确认以上为个人真实约束，可用于生成个性化建议
            </label>
            <div class="toolbar">
              <button class="btn primary sm" :disabled="busy" @click="saveProfile">保存配置</button>
              <span class="tiny muted">风险偏好会决定战略基准权重（见设置页）</span>
            </div>
          </div>
        </SectionCard>
      </div>

      <SectionCard title="基础权重（战略配置）" desc="修改后合计必须为 100%；模型不会因单条新闻擅自改写战略权重">
        <div class="grid g4" style="gap: 10px">
          <label v-for="(value, code) in weightDraft" :key="code" class="small muted">
            {{ targets.find((t) => t.asset_class_code === code)?.name || code }}
            <input v-model.number="weightDraft[code]" type="number" step="0.01" class="input" style="width: 100%; margin-top: 3px" />
          </label>
        </div>
        <div class="toolbar" style="margin-top: 12px">
          <button class="btn primary sm" :disabled="busy" @click="saveWeights">保存基础权重</button>
          <span class="small muted">
            当前合计 {{ (Object.values(weightDraft).reduce((a: number, b: any) => a + Number(b || 0), 0) * 100).toFixed(1) }}%
          </span>
        </div>
      </SectionCard>
    </template>

    <template v-else>
      <div class="grid" style="grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); margin-bottom: 14px">
        <SectionCard title="组合风险" desc="历史统计，不是未来损失上限">
          <dl class="kv" style="grid-template-columns: 118px 1fr">
            <dt>当前波动率</dt><dd>{{ risk?.volatility?.current ? (risk.volatility.current.annual_vol * 100).toFixed(2) + '%' : '不可计算' }}</dd>
            <dt>目标波动率</dt><dd>{{ risk?.volatility?.target ? (risk.volatility.target.annual_vol * 100).toFixed(2) + '%' : '不可计算' }}</dd>
            <dt>波动变化</dt><dd :class="(risk?.vol_change || 0) > 0 ? 'up' : 'down'">{{ risk?.vol_change !== null && risk?.vol_change !== undefined ? fmtPctPoint(risk.vol_change * 100, 2) : '—' }}</dd>
            <dt>历史最大回撤</dt><dd>{{ risk?.max_drawdown_estimate !== null && risk?.max_drawdown_estimate !== undefined ? (risk.max_drawdown_estimate * 100).toFixed(2) + '%' : '不可计算' }}</dd>
            <dt>低流动性权重</dt><dd>{{ ((risk?.low_liquidity_weight || 0) * 100).toFixed(1) }}%</dd>
          </dl>
          <div v-if="risk?.notes?.length" class="banner" style="margin-top: 10px; display: block">
            <div v-for="(n, i) in risk.notes" :key="i" class="tiny">{{ n }}</div>
          </div>
        </SectionCard>

        <SectionCard title="压力情景（线性近似）">
          <table class="table">
            <thead><tr><th>情景</th><th class="right">组合影响</th><th>假设</th></tr></thead>
            <tbody>
              <tr v-for="s in risk?.stress_tests || []" :key="s.name">
                <td>{{ s.name }}</td>
                <td class="right num" :class="s.impact >= 0 ? 'up' : 'down'">{{ fmtPctPoint(s.impact * 100, 2) }}</td>
                <td class="tiny muted">{{ s.assumption }}</td>
              </tr>
            </tbody>
          </table>
        </SectionCard>
      </div>

      <SectionCard title="资产相关性" desc="按代表品种近 250 日收益计算；非日频资产不参与">
        <div v-if="risk?.correlation?.labels?.length" class="table-wrap">
          <table class="table">
            <thead>
              <tr><th></th><th v-for="label in risk.correlation.labels" :key="label" class="center">{{ label }}</th></tr>
            </thead>
            <tbody>
              <tr v-for="(row, i) in risk.correlation.values" :key="i">
                <td class="strong">{{ risk.correlation.labels[i] }}</td>
                <td
                  v-for="(value, j) in row" :key="j" class="center num"
                  :style="{ background: heatColor(value), color: Math.abs(value) > 0.5 ? '#fff' : '#17202f' }"
                >{{ Number(value).toFixed(2) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-else class="empty">历史数据不足，无法计算相关性矩阵</div>
        <template #footer>
          <div v-for="(n, i) in risk?.data_limitations || []" :key="i">{{ n }}</div>
        </template>
      </SectionCard>
    </template>
  </template>
</template>

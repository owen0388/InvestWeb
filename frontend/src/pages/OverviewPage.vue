<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/endpoints'
import SectionCard from '@/components/SectionCard.vue'
import EChart from '@/components/EChart.vue'
import { appState, loadOverview } from '@/stores/app'
import {
  actionClass, changeClass, fmtDate, fmtNum, fmtPctPoint, importanceClass, regimeClass, statusLabel,
} from '@/utils/format'

const data = ref<any>(null)
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.overview()
    appState.overview = data.value
    appState.regime = data.value.regime
    appState.loadedAt = new Date().toISOString()
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

const regime = computed(() => data.value?.regime)
const drivers = computed(() => regime.value?.drivers || [])
const driverOption = computed(() => ({
  grid: { left: 6, right: 34, top: 4, bottom: 0, containLabel: true },
  xAxis: { type: 'value', min: 0, max: 100, splitLine: { lineStyle: { color: '#eef1f6' } }, axisLabel: { color: '#8895a7' } },
  yAxis: {
    type: 'category',
    data: drivers.value.map((d: any) => d.label),
    axisTick: { show: false },
    axisLine: { show: false },
    axisLabel: { color: '#3d4a5c' },
  },
  series: [
    {
      type: 'bar',
      data: drivers.value.map((d: any) => ({
        value: d.value,
        itemStyle: { color: d.value >= 60 ? '#d0342c' : d.value <= 40 ? '#0f8f63' : '#7e8ea3', borderRadius: [0, 2, 2, 0] },
      })),
      barWidth: 12,
      label: { show: true, position: 'right', formatter: '{c}', color: '#67748a', fontSize: 11 },
      markLine: {
        silent: true,
        symbol: 'none',
        data: [
          { xAxis: 38, lineStyle: { color: '#0f8f63', type: 'dashed', width: 1 } },
          { xAxis: 62, lineStyle: { color: '#d0342c', type: 'dashed', width: 1 } },
        ],
        label: { show: false },
      },
    },
  ],
}))

const coverageOption = computed(() => ({
  grid: { left: 6, right: 10, top: 10, bottom: 0, containLabel: true },
  xAxis: {
    type: 'category',
    data: (data.value?.class_snapshot || []).map((c: any) => c.name.slice(0, 5)),
    axisLabel: { color: '#8895a7', fontSize: 10, interval: 0, rotate: 30 },
    axisTick: { show: false },
    axisLine: { lineStyle: { color: '#e3e8f0' } },
  },
  yAxis: { type: 'value', splitLine: { lineStyle: { color: '#eef1f6' } }, axisLabel: { color: '#8895a7', fontSize: 10 } },
  tooltip: { trigger: 'axis' },
  series: [
    {
      name: '有数据品种',
      type: 'bar',
      stack: 'total',
      data: (data.value?.class_snapshot || []).map((c: any) => c.with_data),
      itemStyle: { color: '#1a4f8b' },
      barWidth: 14,
    },
    {
      name: '人工/缺失',
      type: 'bar',
      stack: 'total',
      data: (data.value?.class_snapshot || []).map((c: any) => Math.max(0, c.instrument_count - c.with_data)),
      itemStyle: { color: '#c9d6e6' },
      barWidth: 14,
    },
  ],
}))

const health = computed(() => data.value?.data_health)
const signals = computed(() => data.value?.signals)
</script>

<template>
  <div v-if="loading" class="loading"><span class="spin" /> 正在加载市场总览…</div>
  <div v-else-if="error" class="error-box">{{ error }} <button class="btn sm" style="margin-left: 10px" @click="load">重试</button></div>

  <template v-else>
    <div v-if="data.notices?.length" class="grid" style="margin-bottom: 12px; gap: 8px">
      <div v-for="(notice, i) in data.notices" :key="i" class="banner" :class="notice.level">
        <span>{{ notice.text }}</span>
      </div>
    </div>

    <div class="grid g4" style="margin-bottom: 14px">
      <div class="card kpi">
        <div class="kpi-label">市场风险状态
          <span class="tag" :class="regimeClass(regime?.state)">{{ regime?.state }}</span>
        </div>
        <div class="kpi-value">{{ regime?.score !== null && regime?.score !== undefined ? fmtNum(regime.score, 1) : '—' }}</div>
        <div class="kpi-sub">指标覆盖 {{ regime ? (regime.coverage * 100).toFixed(0) : 0 }}% · 阈值 38 / 62</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">自动跟踪数据覆盖</div>
        <div class="kpi-value">{{ health ? (health.coverage * 100).toFixed(0) : 0 }}<span style="font-size: 14px">%</span></div>
        <div class="kpi-sub">{{ health?.fresh }} / {{ health?.auto_tracked }} 个品种近 3 日有数据</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">可研究机会</div>
        <div class="kpi-value">{{ signals?.actionable ?? 0 }}</div>
        <div class="kpi-sub">评分覆盖 {{ signals?.count ?? 0 }} 个品种 · 截至 {{ signals?.as_of ? fmtDate(signals.as_of) : '—' }}</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">近 72 小时事件</div>
        <div class="kpi-value">{{ data.top_events?.length ?? 0 }}</div>
        <div class="kpi-sub">按重要性排序展示前 5 条</div>
      </div>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1.55fr) minmax(300px, 1fr); margin-bottom: 14px">
      <SectionCard title="主要资产最新表现" desc="数据时间与类型逐条标注；收盘后不再标记为实时">
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>品种</th><th class="right">最新</th><th class="right">涨跌幅</th>
                <th>数据日期</th><th>类型</th><th>状态</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in data.headline" :key="row.symbol">
                <td>
                  <router-link :to="`/assets/${encodeURIComponent(row.symbol)}`">{{ row.name }}</router-link>
                  <div class="sym">{{ row.symbol }}</div>
                </td>
                <td class="right num">{{ row.value !== null ? fmtNum(row.value, row.value > 1000 ? 1 : 3) : '—' }}</td>
                <td class="right num" :class="changeClass(row.change_pct)">{{ row.change_pct !== null ? fmtPctPoint(row.change_pct) : '—' }}</td>
                <td class="muted num">{{ row.trade_date || '—' }}</td>
                <td>
                  <span class="tag" :class="row.data_type === 'realtime' ? 'tag-primary' : row.data_type === 'delayed' ? 'tag-warn' : 'tag-neutral'">
                    {{ row.data_type === 'realtime' ? '实时' : row.data_type === 'delayed' ? '延迟' : row.data_type === 'close' ? '收盘' : '缺失' }}
                  </span>
                </td>
                <td><span class="tag" :class="statusLabel(row.data_status).cls">{{ statusLabel(row.data_status).label }}</span></td>
              </tr>
            </tbody>
          </table>
        </div>
      </SectionCard>

      <SectionCard title="风险状态驱动" :desc="regime?.explanation || ''">
        <EChart v-if="drivers.length" :option="driverOption" height="230px" />
        <div v-else class="empty">暂无可用的风险状态指标</div>
        <div class="list-clean" style="margin-top: 6px">
          <li v-for="d in drivers" :key="d.key" style="padding: 6px 0">
            <div style="display: flex; justify-content: space-between; gap: 8px">
              <span>{{ d.label }}<span class="muted tiny"> · 权重 {{ (d.weight * 100).toFixed(0) }}%</span></span>
              <span class="num strong">{{ fmtNum(d.value, 0) }}</span>
            </div>
            <div class="tiny muted wrap">{{ d.text }}<span v-if="d.as_of"> · {{ d.as_of }}</span></div>
          </li>
        </div>
      </SectionCard>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); margin-bottom: 14px">
      <SectionCard title="资产类别覆盖" desc="目录完整性 ≠ 数据可用性：人工维护与缺失分开统计">
        <EChart :option="coverageOption" height="180px" />
        <div class="table-wrap" style="margin-top: 8px; max-height: 230px">
          <table class="table">
            <thead><tr><th>类别</th><th class="right">品种</th><th class="right">有数据</th><th class="right">人工</th><th>最近更新</th></tr></thead>
            <tbody>
              <tr v-for="c in data.class_snapshot" :key="c.code">
                <td>{{ c.name }}</td>
                <td class="right num">{{ c.instrument_count }}</td>
                <td class="right num">{{ c.with_data }}</td>
                <td class="right num">{{ c.manual }}</td>
                <td class="muted num">{{ c.last_data_at || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </SectionCard>

      <div class="grid" style="gap: 14px">
        <SectionCard title="宏观指标" desc="年度数据，已按陈旧度向中性收缩">
          <div v-if="data.macro?.length" class="grid g2" style="gap: 10px">
            <div v-for="m in data.macro" :key="m.code" style="border: 1px solid var(--line); border-radius: 4px; padding: 9px 10px">
              <div class="tiny muted">{{ m.name }}</div>
              <div class="num strong" style="font-size: 17px">{{ fmtNum(m.value, 2) }}<span class="tiny muted">{{ m.unit }}</span></div>
              <div class="tiny muted">{{ m.period }} 年 · {{ m.source }}</div>
            </div>
          </div>
          <div v-else class="empty">尚未采集宏观数据</div>
        </SectionCard>

        <SectionCard title="最新重要事件">
          <ul v-if="data.top_events?.length" class="list-clean">
            <li v-for="e in data.top_events" :key="e.id">
              <div style="display: flex; justify-content: space-between; gap: 8px">
                <router-link :to="`/events?id=${e.id}`" class="wrap">{{ e.title }}</router-link>
                <span class="tag" :class="importanceClass(e.importance)">{{ e.importance }}</span>
              </div>
              <div class="tiny muted">
                {{ e.category }} · {{ e.published_at ? fmtDate(e.published_at, true) : '时间未知' }} ·
                {{ e.engine === 'rule' ? '规则引擎' : 'AI' }} · 置信度 {{ (e.confidence * 100).toFixed(0) }}%
              </div>
            </li>
          </ul>
          <div v-else class="empty">近期没有达到分析阈值的事件</div>
        </SectionCard>
      </div>
    </div>

    <div class="banner info">
      系统仅提供研究、风险提示与模拟配置，不执行真实下单；评分是同类资产内的相对排序，不代表预期收益。
      数据来源与更新时间见每个数字旁标注及「数据源管理」页。
    </div>
  </template>
</template>

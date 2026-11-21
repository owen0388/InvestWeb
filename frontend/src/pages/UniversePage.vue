<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/endpoints'
import { fmtNum, fmtPctPoint } from '@/utils/format'

const summary = ref<any>({ total: 0, candidate: 0, by_category: [], reject_reasons: {} })
const items = ref<any[]>([])
const meta = ref<any>({ total: 0, pages: 1 })
const loading = ref(true)
const busy = ref('')
const error = ref('')
const notice = ref('')
const page = ref(1)
const expanded = ref<string>('')
const filters = ref({ status: 'candidate', category: '', q: '', sort: 'quality' })

const categoryLabels: Record<string, string> = {
  broad: '宽基',
  sector: '行业主题',
  cross_border: '跨境',
  bond: '债券',
  money: '货币',
  commodity: '商品',
  other: '其他',
}

const scored = computed(() => items.value.filter((i) => i.quality_score != null).length)
const avgQuality = computed(() => {
  const values = items.value.map((i) => i.quality_score).filter((v) => v != null) as number[]
  if (!values.length) return null
  return values.reduce((a, b) => a + b, 0) / values.length
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [list, sum] = await Promise.all([
      api.universe({
        status: filters.value.status || undefined,
        category: filters.value.category || undefined,
        q: filters.value.q || undefined,
        sort: filters.value.sort,
        page: page.value,
        page_size: 25,
      }),
      api.universeSummary(),
    ])
    items.value = list.items
    meta.value = list
    summary.value = sum
  } catch (err: any) {
    error.value = err?.message || '加载候选池失败'
  } finally {
    loading.value = false
  }
}

async function trigger(kind: 'universe' | 'quality') {
  busy.value = kind
  notice.value = ''
  try {
    const res = kind === 'universe' ? await api.refreshUniverse() : await api.refreshQuality()
    notice.value = res?.message || '已提交后台任务'
  } catch (err: any) {
    error.value = err?.message || '触发失败'
  } finally {
    busy.value = ''
  }
}

function coverageDots(row: any) {
  const bd = row.quality_breakdown || {}
  return [
    { key: 'liquidity', on: bd.liquidity?.status === 'available', label: '成交额' },
    { key: 'size', on: bd.size?.status === 'available', label: '规模' },
    { key: 'premium_stability', on: bd.premium_stability?.status === 'available', label: '折溢价历史' },
    { key: 'history_depth', on: bd.history_depth?.status === 'available', label: '日线长度' },
  ]
}

function qualityClass(score: number | null) {
  if (score == null) return 'tag-neutral'
  if (score >= 75) return 'tag-down'
  if (score >= 50) return 'tag-warn'
  return 'tag-danger'
}

function statusLabel(status: string) {
  return status === 'candidate' ? '候选' : '已拒绝'
}

function applyFilter() {
  page.value = 1
  load()
}

onMounted(load)
</script>

<template>
  <div class="section-head">
    <div>
      <h2>ETF 候选池</h2>
      <div class="section-desc">
        C 层研究范围：全市场登记 → 准入门槛 → 同指数去重 → 质量分。数量是规则的结果，不是目标。
        质量分只评价「产品好不好用」，不评价「板块值不值得买」。
      </div>
    </div>
    <div class="pill-row">
      <button class="btn sm" :disabled="!!busy" @click="trigger('universe')">
        {{ busy === 'universe' ? '提交中…' : '刷新候选池' }}
      </button>
      <button class="btn sm primary" :disabled="!!busy" @click="trigger('quality')">
        {{ busy === 'quality' ? '提交中…' : '计算质量分' }}
      </button>
    </div>
  </div>

  <div v-if="summary.session_note" class="warn-banner" style="margin-bottom: 12px">
    {{ summary.session_note }}
  </div>
  <div v-if="notice" class="notice-banner" style="margin-bottom: 12px">{{ notice }}</div>
  <div v-if="error" class="error-box" style="margin-bottom: 12px">{{ error }}</div>

  <div class="grid g4" style="margin-bottom: 14px">
    <div class="card kpi">
      <div class="kpi-label">全量登记</div>
      <div class="kpi-value">{{ summary.total }}</div>
      <div class="kpi-sub">含被拒绝记录，原因可查</div>
    </div>
    <div class="card kpi">
      <div class="kpi-label">候选（C 层）</div>
      <div class="kpi-value">{{ summary.candidate }}</div>
      <div class="kpi-sub">进入评分与策略的唯一范围</div>
    </div>
    <div class="card kpi">
      <div class="kpi-label">本页已评分</div>
      <div class="kpi-value">{{ scored }}<span style="font-size: 14px; color: var(--muted)"> / {{ items.length }}</span></div>
      <div class="kpi-sub">未评分显示为空，不按 0 处理</div>
    </div>
    <div class="card kpi">
      <div class="kpi-label">平均质量分</div>
      <div class="kpi-value">{{ avgQuality == null ? '—' : avgQuality.toFixed(1) }}</div>
      <div class="kpi-sub">仅统计本页有分数的标的</div>
    </div>
  </div>

  <div class="card" style="margin-bottom: 14px">
    <div class="card-head"><div class="card-title">分类分布</div></div>
    <div class="card-body tight">
      <div class="pill-row">
        <button
          class="tag"
          :class="filters.category === '' ? 'tag-down' : 'tag-neutral'"
          @click="filters.category = ''; applyFilter()"
        >
          全部 {{ summary.candidate }}
        </button>
        <button
          v-for="c in summary.by_category"
          :key="c.category"
          class="tag"
          :class="filters.category === c.category ? 'tag-down' : 'tag-neutral'"
          @click="filters.category = c.category; applyFilter()"
        >
          {{ categoryLabels[c.category] || c.category }} {{ c.candidate }}
        </button>
      </div>
      <div class="small muted" style="margin-top: 8px">
        被拒原因：
        <span v-for="(count, reason) in summary.reject_reasons" :key="reason" style="margin-right: 10px">
          {{ reason }} {{ count }}
        </span>
      </div>
    </div>
  </div>

  <div class="card" style="margin-bottom: 14px">
    <div class="card-body tight">
      <div class="toolbar">
        <input
          v-model="filters.q"
          class="input wide"
          placeholder="搜索代码或名称"
          @keyup.enter="applyFilter"
        />
        <select v-model="filters.status" class="select" @change="applyFilter">
          <option value="candidate">仅候选</option>
          <option value="">全部记录</option>
          <option value="rejected">仅被拒绝</option>
        </select>
        <select v-model="filters.sort" class="select" @change="applyFilter">
          <option value="quality">按质量分</option>
          <option value="default">按状态/成交额</option>
          <option value="category">按类别</option>
        </select>
        <div class="spacer" />
        <button class="btn sm" @click="applyFilter">查询</button>
      </div>
    </div>
  </div>

  <div class="card">
    <div v-if="loading" class="loading"><span class="spin" /> 加载候选池…</div>
    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>代码 / 名称</th>
            <th>类别</th>
            <th class="right">最新价</th>
            <th class="right">涨跌幅</th>
            <th class="right">成交额</th>
            <th class="right">规模</th>
            <th class="right">折溢价</th>
            <th class="right">质量分</th>
            <th>覆盖</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="row in items" :key="row.code">
            <tr style="cursor: pointer" @click="expanded = expanded === row.code ? '' : row.code">
              <td>
                <div class="strong">{{ row.name }}</div>
                <div class="mono tiny muted">{{ row.code }} · {{ row.symbol }}</div>
              </td>
              <td>
                <span class="tag tag-neutral">{{ row.category_label }}</span>
              </td>
              <td class="right num">{{ row.price == null ? '—' : fmtNum(row.price, 3) }}</td>
              <td class="right num" :class="(row.change_pct || 0) >= 0 ? 'up' : 'down'">
                {{ row.change_pct == null ? '—' : fmtPctPoint(row.change_pct) }}
              </td>
              <td class="right num">
                {{ row.amount == null ? '—' : (row.amount / 1e8).toFixed(2) + ' 亿' }}
                <span class="tiny muted">/1d</span>
              </td>
              <td class="right num">
                {{ row.mktcap == null ? '—' : (row.mktcap / 1e8).toFixed(1) + ' 亿' }}
              </td>
              <td class="right num">
                {{ row.premium_est == null ? '—' : (row.premium_est * 100).toFixed(2) + '%' }}
              </td>
              <td class="right num">
                <span v-if="row.quality_score == null" class="muted">未评分</span>
                <span v-else class="tag" :class="qualityClass(row.quality_score)">
                  {{ row.quality_score.toFixed(1) }}
                </span>
                <div v-if="row.quality_confidence != null && row.quality_confidence < 1" class="tiny muted">
                  置信 {{ (row.quality_confidence * 100).toFixed(0) }}%
                </div>
              </td>
              <td>
                <span class="dot-row">
                  <span
                    v-for="dot in coverageDots(row)"
                    :key="dot.key"
                    class="cov-dot"
                    :class="{ on: dot.on }"
                    :title="dot.label + (dot.on ? '：已覆盖' : '：未覆盖')"
                  />
                </span>
              </td>
              <td>
                <span class="tag" :class="row.status === 'candidate' ? 'tag-down' : 'tag-muted'">
                  {{ statusLabel(row.status) }}
                </span>
              </td>
            </tr>
            <tr v-if="expanded === row.code" :key="row.code + '-detail'">
              <td colspan="10" style="background: #fafbfd">
                <div class="grid" style="grid-template-columns: 1.4fr 1fr; gap: 16px">
                  <div>
                    <div class="strong small" style="margin-bottom: 6px">质量分构成</div>
                    <table class="table tiny-table">
                      <thead>
                        <tr>
                          <th>指标</th>
                          <th class="right">取值</th>
                          <th class="right">分位分</th>
                          <th class="right">权重</th>
                          <th>状态</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr v-for="(metric, key) in row.quality_breakdown" :key="key">
                          <td>{{ metric.label }}</td>
                          <td class="right num">
                            {{ metric.value == null ? '—' : metric.value }}
                          </td>
                          <td class="right num">{{ metric.score == null ? '—' : metric.score.toFixed(1) }}</td>
                          <td class="right num">{{ metric.weight ? (metric.weight * 100).toFixed(0) + '%' : '—' }}</td>
                          <td>
                            <span class="tag" :class="metric.status === 'available' ? 'tag-down' : 'tag-muted'">
                              {{
                                metric.status === 'available'
                                  ? '已覆盖'
                                  : metric.status === 'disabled'
                                    ? '覆盖率不足，已停用'
                                    : '未覆盖'
                              }}
                            </span>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                  <div>
                    <div class="strong small" style="margin-bottom: 6px">基础信息</div>
                    <div class="small" style="line-height: 1.9">
                      <div>跟踪指数：{{ row.tracked_index || '未获取' }}</div>
                      <div>上市日期：{{ row.list_date || '—' }}（{{ row.listed_days ?? '—' }} 天）</div>
                      <div>单位净值：{{ row.nav ?? '—' }}　报告期净资产：{{ row.endnav ? (row.endnav / 1e8).toFixed(1) + ' 亿' : '—' }}</div>
                      <div>成交额口径：{{ row.amount_basis === '1d' ? '当日成交额（未用日线反推）' : row.amount_basis }}</div>
                      <div v-if="row.reject_reasons?.length">拒绝原因：{{ row.reject_reasons.join('；') }}</div>
                    </div>
                  </div>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="!items.length">
            <td colspan="10" class="muted" style="text-align: center; padding: 28px">
              没有符合条件的记录。若候选池为空，请先点「刷新候选池」。
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="card-foot toolbar">
      <span class="muted small">共 {{ meta.total }} 条 · 第 {{ meta.page }} / {{ meta.pages }} 页</span>
      <div class="spacer" />
      <button class="btn sm" :disabled="page <= 1" @click="page--; load()">上一页</button>
      <button class="btn sm" :disabled="page >= meta.pages" @click="page++; load()">下一页</button>
    </div>
  </div>
</template>

<style scoped>
.notice-banner {
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--primary-soft);
  border: 1px solid #d5e3f2;
  color: var(--primary);
  font-size: 13px;
}
.warn-banner {
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--warn-soft);
  border: 1px solid #f0e0c2;
  color: var(--warn);
  font-size: 13px;
}
.dot-row {
  display: inline-flex;
  gap: 4px;
}
.cov-dot {
  width: 8px;
  height: 8px;
  border-radius: 2px;
  background: #e3e8ef;
  border: 1px solid #d5dbe5;
  display: inline-block;
}
.cov-dot.on {
  background: #2f6fed;
  border-color: #2f6fed;
}
.tiny-table th,
.tiny-table td {
  padding: 5px 8px;
  font-size: 12px;
}
</style>

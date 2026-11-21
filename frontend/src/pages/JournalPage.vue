<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/endpoints'

const stats = ref<any>(null)
const rows = ref<any[]>([])
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const notice = ref('')

const form = ref({
  symbol: '',
  name: '',
  decision: 'followed',
  actual_price: '',
  quantity: '',
  fee: '0',
  thesis: '',
  invalidation: '',
})

const closing = ref<any>(null)
const closeForm = ref({ exit_price: '', exit_reason: 'target', fee: '0' })

const reliable = computed(() => stats.value?.overall?.reliable === true)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [list, st] = await Promise.all([api.journal({ limit: 100 }), api.journalStats()])
    rows.value = list.items
    stats.value = st
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function submit() {
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const body = {
      symbol: form.value.symbol.trim(),
      name: form.value.name.trim(),
      decision: form.value.decision,
      actual_price: Number(form.value.actual_price),
      quantity: Number(form.value.quantity),
      fee: Number(form.value.fee || 0),
      thesis: form.value.thesis,
      invalidation: form.value.invalidation,
      source: 'manual',
    }
    await api.addJournal(body)
    notice.value = `已记录 ${body.symbol}`
    form.value = {
      symbol: '', name: '', decision: 'followed', actual_price: '',
      quantity: '', fee: '0', thesis: '', invalidation: '',
    }
    await load()
  } catch (err: any) {
    error.value = err?.message || '录入失败'
  } finally {
    saving.value = false
  }
}

function openClose(row: any) {
  closing.value = row
  closeForm.value = { exit_price: '', exit_reason: 'target', fee: '0' }
}

async function submitClose() {
  if (!closing.value) return
  saving.value = true
  error.value = ''
  try {
    await api.closeJournal(closing.value.id, {
      exit_price: Number(closeForm.value.exit_price),
      exit_reason: closeForm.value.exit_reason,
      fee: Number(closeForm.value.fee || 0),
    })
    closing.value = null
    await load()
  } catch (err: any) {
    error.value = err?.message || '平仓失败'
  } finally {
    saving.value = false
  }
}

async function remove(row: any) {
  if (!confirm(`删除 ${row.symbol} 的这条记录？`)) return
  await api.deleteJournal(row.id)
  await load()
}

const decisionLabel: Record<string, string> = {
  followed: '照信号执行',
  skipped: '信号未执行',
  manual: '自主决定',
}

onMounted(load)
</script>

<template>
  <div class="section-head">
    <div>
      <h2>交易日志与归因</h2>
      <div class="section-desc">
        记录「信号 → 是否执行 → 实际成交 → 结果」。没有执行标记，就永远分不清是策略差还是没执行。
      </div>
    </div>
    <div class="pill-row"><button class="btn sm" @click="load">刷新</button></div>
  </div>

  <div v-if="error" class="error-box" style="margin-bottom: 12px">{{ error }}</div>
  <div v-if="notice" class="small" style="margin-bottom: 12px; color: var(--down)">{{ notice }}</div>

  <div v-if="stats" class="grid g4" style="margin-bottom: 14px">
    <div class="card kpi">
      <div class="kpi-label">已平仓样本</div>
      <div class="kpi-value">{{ stats.overall.sample }}</div>
      <div class="kpi-sub">
        <span :class="reliable ? 'down' : 'muted'" style="color: var(--warn)">
          {{ reliable ? '样本充足' : `不足 ${stats.min_reliable_sample} 笔，数字仅供参考` }}
        </span>
      </div>
    </div>
    <div class="card kpi">
      <div class="kpi-label">胜率</div>
      <div class="kpi-value">{{ stats.overall.win_rate == null ? '—' : (stats.overall.win_rate * 100).toFixed(0) + '%' }}</div>
      <div class="kpi-sub">盈利 {{ stats.overall.wins ?? 0 }} / 亏损 {{ stats.overall.losses ?? 0 }}</div>
    </div>
    <div class="card kpi">
      <div class="kpi-label">盈亏比</div>
      <div class="kpi-value">{{ stats.overall.payoff_ratio ?? '—' }}</div>
      <div class="kpi-sub">平均盈利 ÷ 平均亏损</div>
    </div>
    <div class="card kpi">
      <div class="kpi-label">期望收益</div>
      <div class="kpi-value">{{ stats.overall.expectancy ?? '—' }}</div>
      <div class="kpi-sub">每笔平均，含费用</div>
    </div>
  </div>

  <div v-if="stats?.warnings?.length" class="card" style="margin-bottom: 14px">
    <div class="card-body tight small" style="color: var(--warn)">
      <div v-for="(w, i) in stats.warnings" :key="i">· {{ w }}</div>
      <div v-if="stats.comparison" style="margin-top: 6px; color: var(--text-2)">{{ stats.comparison }}</div>
    </div>
  </div>

  <div class="card" style="margin-bottom: 14px">
    <div class="card-head"><div class="card-title">录入一笔</div></div>
    <div class="card-body tight">
      <div class="toolbar">
        <input v-model="form.symbol" class="input" placeholder="代码 如 sh512690" style="width: 150px" />
        <input v-model="form.name" class="input" placeholder="名称" style="width: 130px" />
        <select v-model="form.decision" class="select">
          <option value="followed">照信号执行</option>
          <option value="skipped">信号未执行</option>
          <option value="manual">自主决定</option>
        </select>
        <input v-model="form.actual_price" class="input" placeholder="成交价" style="width: 90px" />
        <input v-model="form.quantity" class="input" placeholder="数量" style="width: 90px" />
        <input v-model="form.fee" class="input" placeholder="费用" style="width: 70px" />
      </div>
      <div class="toolbar" style="margin-top: 8px">
        <input v-model="form.thesis" class="input wide" placeholder="买入理由" />
        <input
          v-model="form.invalidation"
          class="input wide"
          placeholder="失效条件（必填）：什么情况下承认判断错了"
        />
        <button class="btn sm primary" :disabled="saving" @click="submit">记录</button>
      </div>
    </div>
  </div>

  <div class="card">
    <div v-if="loading" class="loading"><span class="spin" /> 加载…</div>
    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th>代码 / 名称</th><th>执行</th><th class="right">成交价</th><th class="right">数量</th>
            <th class="right">盈亏</th><th class="right">持有天数</th><th>状态</th><th class="right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id">
            <td>
              <div class="strong">{{ row.name || row.symbol }}</div>
              <div class="mono tiny muted">{{ row.symbol }}</div>
            </td>
            <td><span class="tag tag-neutral">{{ decisionLabel[row.decision] || row.decision }}</span></td>
            <td class="right num">{{ row.actual_price }}</td>
            <td class="right num">{{ row.quantity }}</td>
            <td class="right num" :class="(row.pnl ?? 0) >= 0 ? 'up' : 'down'">
              {{ row.pnl == null ? '—' : row.pnl }}
              <span v-if="row.pnl_pct != null" class="tiny">({{ (row.pnl_pct * 100).toFixed(2) }}%)</span>
            </td>
            <td class="right num">{{ row.holding_days ?? '—' }}</td>
            <td>
              <span class="tag" :class="row.status === 'open' ? 'tag-warn' : 'tag-muted'">
                {{ row.status === 'open' ? '持仓中' : '已平仓' }}
              </span>
            </td>
            <td class="right">
              <button v-if="row.status === 'open'" class="btn sm" @click="openClose(row)">平仓</button>
              <button class="btn sm" style="margin-left: 6px" @click="remove(row)">删除</button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="8" class="muted" style="text-align: center; padding: 28px">
              还没有记录。每笔交易都值得记一笔，尤其是「看到信号但没做」的那种。
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <div v-if="closing" class="drawer-mask" @click.self="closing = null">
    <div class="drawer" style="width: 420px">
      <div class="drawer-head">
        <div class="strong">平仓 · {{ closing.name || closing.symbol }}</div>
        <button class="btn sm" @click="closing = null">关闭</button>
      </div>
      <div class="drawer-body">
        <div class="small muted" style="margin-bottom: 8px">开仓价 {{ closing.actual_price }}</div>
        <input v-model="closeForm.exit_price" class="input wide" placeholder="平仓价" style="margin-bottom: 8px" />
        <select v-model="closeForm.exit_reason" class="select" style="margin-bottom: 8px">
          <option value="target">达到目标</option>
          <option value="invalidation">触发失效条件</option>
          <option value="stop_loss">止损</option>
          <option value="manual">主动调整</option>
        </select>
        <input v-model="closeForm.fee" class="input wide" placeholder="平仓费用" style="margin-bottom: 12px" />
        <button class="btn sm primary" :disabled="saving" @click="submitClose">确认平仓</button>
      </div>
    </div>
  </div>
</template>

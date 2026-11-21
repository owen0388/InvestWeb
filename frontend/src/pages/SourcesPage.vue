<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '@/api/endpoints'
import SectionCard from '@/components/SectionCard.vue'
import { fmtDate, relativeTime, statusLabel } from '@/utils/format'

const sources = ref<any[]>([])
const jobs = ref<any>({ jobs: [] })
const loading = ref(true)
const error = ref('')
const message = ref('')
const busy = ref<string>('')
const expanded = ref<string>('')
const runs = ref<any[]>([])

async function load() {
  loading.value = true
  try {
    const [src, jb] = await Promise.all([api.sources(), api.jobs()])
    sources.value = src.items
    jobs.value = jb
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function runSource(code: string) {
  busy.value = code
  message.value = ''
  try {
    const res = await api.runSource(code)
    message.value = `${code}：${res.message}${res.errors?.length ? '（' + res.errors[0] + '）' : ''}`
    await load()
    if (expanded.value === code) await showRuns(code)
  } catch (err: any) {
    message.value = err?.message || '触发失败'
  } finally {
    busy.value = ''
  }
}

async function toggle(source: any) {
  try {
    await api.updateSource(source.code, { enabled: !source.enabled })
    await load()
  } catch (err: any) {
    message.value = err?.message || '更新失败'
  }
}

async function showRuns(code: string) {
  if (expanded.value === code) {
    expanded.value = ''
    return
  }
  expanded.value = code
  try {
    const res = await api.sourceRuns(code)
    runs.value = res.items
  } catch {
    runs.value = []
  }
}

async function runJob(id: string) {
  busy.value = id
  message.value = ''
  try {
    const res = await api.runJob(id)
    message.value = `${id}：${res.status === 'success' ? '执行成功' : '执行失败 ' + (res.detail?.error || '')}`
    await load()
  } catch (err: any) {
    message.value = err?.message || '任务触发失败'
  } finally {
    busy.value = ''
  }
}
</script>

<template>
  <div class="section-head">
    <div>
      <h2>数据源管理</h2>
      <div class="section-desc">
        每个数据源独立记录状态、最近成功时间、错误与频率。缺少密钥或依赖时明确显示原因，不伪造数据。
      </div>
    </div>
    <button class="btn sm" @click="load">刷新</button>
  </div>

  <div v-if="message" class="banner info" style="margin-bottom: 12px">{{ message }}</div>
  <div v-if="error" class="error-box" style="margin-bottom: 12px">{{ error }}</div>

  <div class="card" style="margin-bottom: 14px">
    <div class="card-head"><div class="card-title">数据源状态</div></div>
    <div v-if="loading" class="loading"><span class="spin" /> 加载中…</div>
    <div v-else class="table-wrap">
      <table class="table">
        <thead>
          <tr><th>数据源</th><th>类别</th><th>状态</th><th>最近成功</th><th>频率</th><th>最近错误/说明</th><th class="right">操作</th></tr>
        </thead>
        <tbody>
          <template v-for="s in sources" :key="s.code">
            <tr>
              <td>
                <div class="strong">{{ s.name }}</div>
                <div class="sym">{{ s.code }}</div>
              </td>
              <td class="small">{{ s.category }}</td>
              <td><span class="tag" :class="statusLabel(s.status).cls">{{ statusLabel(s.status).label }}</span></td>
              <td class="small muted nowrap">{{ s.last_success_at ? relativeTime(s.last_success_at) : '从未' }}</td>
              <td class="small muted">{{ s.interval_minutes ? s.interval_minutes + ' 分钟' : '按需' }}</td>
              <td class="small muted wrap" style="max-width: 320px">
                <span v-if="s.last_error" class="down">{{ s.last_error.slice(0, 120) }}</span>
                <span v-else>{{ s.status_message }}</span>
              </td>
              <td class="right nowrap">
                <button class="btn sm ghost" @click="showRuns(s.code)">记录</button>
                <button
                  v-if="['sina_quote', 'eastmoney_kline', 'eastmoney_news', 'worldbank_macro', 'fred_macro'].includes(s.code)"
                  class="btn sm" :disabled="busy === s.code" @click="runSource(s.code)"
                >
                  {{ busy === s.code ? '执行中' : '立即采集' }}
                </button>
                <button class="btn sm ghost" @click="toggle(s)">{{ s.enabled ? '停用' : '启用' }}</button>
              </td>
            </tr>
            <tr v-if="expanded === s.code">
              <td colspan="7" style="background: #fafbfd">
                <div class="tiny muted" style="margin-bottom: 6px">最近执行记录</div>
                <table class="table">
                  <thead><tr><th>任务</th><th>触发</th><th>状态</th><th>开始</th><th class="right">耗时</th><th class="right">记录</th><th>错误</th></tr></thead>
                  <tbody>
                    <tr v-for="r in runs" :key="r.id">
                      <td class="small">{{ r.job_name }}</td>
                      <td class="small">{{ r.trigger }}</td>
                      <td><span class="tag" :class="statusLabel(r.status).cls">{{ statusLabel(r.status).label }}</span></td>
                      <td class="small muted">{{ fmtDate(r.started_at, true) }}</td>
                      <td class="right num small">{{ r.duration_ms !== null ? r.duration_ms + ' ms' : '—' }}</td>
                      <td class="right num small">{{ r.records }}</td>
                      <td class="small down wrap" style="max-width: 260px">{{ r.error }}</td>
                    </tr>
                    <tr v-if="!runs.length"><td colspan="7" class="empty">暂无执行记录</td></tr>
                  </tbody>
                </table>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </div>

  <div class="grid" style="grid-template-columns: minmax(0, 1.5fr) minmax(300px, 1fr)">
    <SectionCard title="后台任务" :desc="jobs.running ? `调度器运行中 · 时区 ${jobs.timezone}` : '调度器未启动'">
      <div class="table-wrap">
        <table class="table">
          <thead><tr><th>任务</th><th>下次执行</th><th>上次状态</th><th>说明</th><th class="right">操作</th></tr></thead>
          <tbody>
            <tr v-for="j in jobs.jobs" :key="j.id">
              <td>{{ j.name }}<div class="sym">{{ j.id }}</div></td>
              <td class="small muted nowrap">{{ j.next_run_at || '—' }}</td>
              <td>
                <span v-if="j.last_run" class="tag" :class="statusLabel(j.last_run.status).cls">{{ statusLabel(j.last_run.status).label }}</span>
                <span v-else class="tag tag-muted">未运行</span>
              </td>
              <td class="small muted wrap" style="max-width: 280px">{{ j.description }}</td>
              <td class="right"><button class="btn sm" :disabled="busy === j.id" @click="runJob(j.id)">运行</button></td>
            </tr>
          </tbody>
        </table>
      </div>
    </SectionCard>

    <SectionCard title="权限与限制说明">
      <ul class="small" style="padding-left: 16px; margin: 0; color: var(--text-2)">
        <li style="margin-bottom: 6px">新浪行情、东方财富日线与新闻、世界银行数据为公开接口，适合个人研究，不保证 SLA，请遵守频率限制。</li>
        <li style="margin-bottom: 6px">FRED 需要免费 API Key（<code>FRED_API_KEY</code>）；Tushare 需要 Token 与积分（<code>TUSHARE_TOKEN</code>）。</li>
        <li style="margin-bottom: 6px">AKShare 需额外安装 <code>akshare</code> 包，未安装时状态显示为缺少依赖。</li>
        <li style="margin-bottom: 6px">GDELT 默认关闭，启用后受每 5 秒 1 次的限制，可能返回 429。</li>
        <li>本系统不抓取个人账户数据，也不执行任何真实交易。</li>
      </ul>
    </SectionCard>
  </div>
</template>

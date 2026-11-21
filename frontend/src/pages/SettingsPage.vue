<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/endpoints'
import SectionCard from '@/components/SectionCard.vue'
import { fmtNum, statusLabel } from '@/utils/format'

const settings = ref<any>(null)
const usage = ref<any>(null)
const loading = ref(true)
const error = ref('')
const message = ref('')
const saving = ref(false)
const threshold = ref(72)
const notificationSettings = ref<any>({})

async function load() {
  loading.value = true
  try {
    const [s, u] = await Promise.all([api.settings(), api.usage()])
    settings.value = s
    usage.value = u
    notificationSettings.value = s.settings?.notifications || {}
    threshold.value = notificationSettings.value.min_importance ?? 72
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function saveNotification() {
  saving.value = true
  message.value = ''
  try {
    const res = await api.updateSettings({
      notification_threshold: Number(threshold.value),
      notifications: { ...notificationSettings.value, min_importance: Number(threshold.value) },
    })
    settings.value = res
    message.value = '通知阈值已保存'
  } catch (err: any) {
    message.value = err?.message || '保存失败'
  } finally {
    saving.value = false
  }
}

const meta = computed(() => settings.value?.meta || {})
const budget = computed(() => usage.value?.budget)
const dailyPct = computed(() => {
  if (!budget.value?.daily_limit) return 0
  return Math.min(100, (budget.value.today_usd / budget.value.daily_limit) * 100)
})
const monthlyPct = computed(() => {
  if (!budget.value?.monthly_limit) return 0
  return Math.min(100, (budget.value.month_usd / budget.value.monthly_limit) * 100)
})
</script>

<template>
  <div class="section-head">
    <div>
      <h2>系统设置</h2>
      <div class="section-desc">
        模型、数据源密钥与调度开关通过环境变量配置（.env），界面展示当前生效状态；通知阈值可在线修改。
      </div>
    </div>
  </div>

  <div v-if="loading" class="loading"><span class="spin" /> 加载配置…</div>
  <div v-else-if="error" class="error-box">{{ error }}</div>

  <template v-else>
    <div v-if="message" class="banner info" style="margin-bottom: 12px">{{ message }}</div>

    <div class="grid" style="grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); margin-bottom: 14px">
      <SectionCard title="模型与成本控制" desc="未配置密钥时 AI 分析显示未配置，系统使用规则引擎，不伪造 AI 结果">
        <dl class="kv" style="grid-template-columns: 116px 1fr">
          <dt>启用状态</dt>
          <dd>
            <span class="tag" :class="meta.llm?.enabled && meta.llm?.api_key_configured ? 'tag-down' : 'tag-warn'">
              {{ meta.llm?.enabled && meta.llm?.api_key_configured ? '已启用' : '未配置 / 未启用' }}
            </span>
          </dd>
          <dt>供应商</dt><dd>{{ meta.llm?.base_url || '未设置 LLM_BASE_URL' }}</dd>
          <dt>低成本模型</dt><dd>{{ meta.llm?.cheap_model || '—' }}</dd>
          <dt>高能力模型</dt><dd>{{ meta.llm?.strong_model || '—' }}</dd>
          <dt>密钥</dt><dd>{{ meta.llm?.api_key_configured ? '已配置（不显示明文）' : '未配置 LLM_API_KEY' }}</dd>
          <dt>今日消耗</dt><dd class="num">${{ fmtNum(budget?.today_usd, 4) }} / ${{ fmtNum(budget?.daily_limit, 2) }}</dd>
          <dt>本月消耗</dt><dd class="num">${{ fmtNum(budget?.month_usd, 4) }} / ${{ fmtNum(budget?.monthly_limit, 2) }}</dd>
        </dl>
        <div style="margin-top: 10px">
          <div class="tiny muted">今日预算使用</div>
          <div class="bar" style="margin: 3px 0 10px"><span :style="{ width: dailyPct + '%', background: dailyPct > 80 ? '#b0741a' : undefined }" /></div>
          <div class="tiny muted">本月预算使用</div>
          <div class="bar" style="margin-top: 3px"><span :style="{ width: monthlyPct + '%', background: monthlyPct > 80 ? '#b0741a' : undefined }" /></div>
        </div>
        <div class="banner" style="margin-top: 10px">
          修改模型或预算请编辑项目根目录 <code>.env</code>：LLM_ENABLED / LLM_BASE_URL / LLM_API_KEY /
          LLM_MODEL_CHEAP / LLM_MODEL_STRONG / LLM_DAILY_BUDGET_USD，然后重启后端。
        </div>
      </SectionCard>

      <SectionCard title="通知设置" desc="只有达到阈值的事件才通知，同一事件 12 小时内只发送一次">
        <div class="toolbar" style="margin-bottom: 10px">
          <label class="small muted">重要性阈值</label>
          <input v-model.number="threshold" type="number" min="0" max="100" class="input" style="width: 90px" />
          <button class="btn primary sm" :disabled="saving" @click="saveNotification">保存</button>
        </div>
        <div class="bar"><span :style="{ width: threshold + '%' }" /></div>
        <div class="tiny muted" style="margin: 4px 0 10px">当前阈值 {{ threshold }}：重要性低于该值的事件只记录、不提醒</div>
        <dl class="kv" style="grid-template-columns: 116px 1fr">
          <dt>站内消息</dt><dd>启用（数据库通知表）</dd>
          <dt>Webhook</dt><dd>{{ meta.notifications?.webhook_configured ? '已配置 WEBHOOK_URL' : '未配置 WEBHOOK_URL' }}</dd>
          <dt>邮件</dt><dd>{{ meta.notifications?.email_configured ? '已配置 SMTP' : '未配置 SMTP（首期预留）' }}</dd>
        </dl>
        <div class="toolbar" style="margin-top: 10px">
          <button class="btn sm" @click="api.dispatchNotifications().then(() => (message = '已处理待发送通知'))">发送待发送通知</button>
          <button class="btn sm" @click="api.digest().then(() => (message = '已生成今日简报'))">生成每日简报</button>
        </div>
      </SectionCard>
    </div>

    <div class="grid" style="grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); margin-bottom: 14px">
      <SectionCard title="数据与权限">
        <dl class="kv" style="grid-template-columns: 130px 1fr">
          <dt>FRED API Key</dt><dd>{{ meta.data_sources?.fred_configured ? '已配置' : '未配置（宏观仅世界银行）' }}</dd>
          <dt>Tushare Token</dt><dd>{{ meta.data_sources?.tushare_configured ? '已配置' : '未配置（可选适配器）' }}</dd>
          <dt>GDELT</dt><dd>{{ meta.data_sources?.gdelt_enabled ? '已启用' : '未启用（GDELT_ENABLED=0）' }}</dd>
          <dt>调度器</dt><dd>{{ meta.scheduler?.enabled ? '随服务启动（单进程）' : '未启用' }} · {{ meta.scheduler?.timezone }}</dd>
          <dt>配置版本</dt><dd>{{ meta.config_version }}</dd>
          <dt>保留策略</dt><dd>行情 900 天 / 新闻 180 天 / 运行记录 60 天（当前需手工清理，未实现自动删除）</dd>
        </dl>
        <div class="banner warn" style="margin-top: 10px">
          密钥只通过环境变量读取，不会写入数据库或返回给前端。切换 PostgreSQL 需修改 DATABASE_URL 并迁移表结构。
        </div>
      </SectionCard>

      <SectionCard title="Token 使用统计" :desc="`近 14 天 ${usage?.totals?.calls || 0} 次调用 · ${usage?.totals?.tokens || 0} tokens`">
        <table class="table">
          <thead><tr><th>模型</th><th class="right">调用</th><th class="right">Tokens</th><th class="right">成本</th></tr></thead>
          <tbody>
            <tr v-for="m in usage?.by_model || []" :key="m.model">
              <td>{{ m.model }}</td>
              <td class="right num">{{ m.calls }}</td>
              <td class="right num">{{ m.tokens }}</td>
              <td class="right num">${{ fmtNum(m.cost, 4) }}</td>
            </tr>
            <tr v-if="!usage?.by_model?.length"><td colspan="4" class="empty">暂无模型调用记录（未配置密钥时不会产生调用）</td></tr>
          </tbody>
        </table>
        <template #footer>
          失败 {{ usage?.totals?.failed || 0 }} 次 · 缓存命中 {{ usage?.totals?.cached || 0 }} 次；相同内容哈希不会重复调用。
        </template>
      </SectionCard>
    </div>

    <SectionCard title="使用建议">
      <ul class="small" style="padding-left: 16px; margin: 0; color: var(--text-2)">
        <li>先在「数据源管理」依次触发 宏观 → 日线历史 → 行情 → 新闻；再运行「资产评分更新」。</li>
        <li>「资产配置」页的基础权重是可解释的战略配置，模型只做有限战术倾斜，并经过约束投影。</li>
        <li>回测仅使用趋势/动量/波动信号，避免使用当前宏观数据回溯历史造成前视偏差。</li>
        <li>任何评分、事件方向与配置建议都不构成投资建议，真实交易需自行判断。</li>
      </ul>
    </SectionCard>
  </template>
</template>

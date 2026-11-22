<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '@/api/endpoints'
import DataFreshness from '@/components/DataFreshness.vue'

const data = ref<any>({ items: [], buckets: {}, bucket_labels: {} })
const loading = ref(true)
const error = ref('')
const expanded = ref<string>('')

const bucketList = computed(() => {
  const labels = data.value.bucket_labels || {}
  return Object.entries(data.value.buckets || {}).map(([key, value]) => ({
    key,
    label: labels[key] || key,
    value: value as number,
  }))
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.radarDaily(10)
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function actionClass(action: string) {
  if (action === '高优先级研究') return 'tag-up'
  if (action === '重点关注') return 'tag-primary'
  if (action === '观察') return 'tag-neutral'
  return 'tag-muted'
}

function pct(value: number | null, digits = 1) {
  return value == null ? '—' : (value * 100).toFixed(digits) + '%'
}

onMounted(load)
</script>

<template>
  <div class="section-head">
    <div>
      <h2>机会雷达</h2>
      <div class="section-desc">
        每日 0–10 条，经过质量门槛、机会分门槛、同主题去重、类别配额四道过滤。
        没有合格标的时会明确说明原因，不会用空列表充数。
      </div>
    </div>
  </div>

  <DataFreshness @refreshed="load" />

  <div v-if="error" class="error-box" style="margin-bottom: 12px">{{ error }}</div>

  <div v-if="loading" class="loading"><span class="spin" /> 计算中…</div>

  <template v-else>
    <div class="grid g4" style="margin-bottom: 14px">
      <div class="card kpi">
        <div class="kpi-label">候选池</div>
        <div class="kpi-value">{{ data.universe }}</div>
        <div class="kpi-sub">已通过准入门槛的 ETF</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">参与评分</div>
        <div class="kpi-value">{{ data.evaluated }}</div>
        <div class="kpi-sub">已排除质量分不足与货币类</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">达标</div>
        <div class="kpi-value">{{ data.qualified }}</div>
        <div class="kpi-sub">机会分 ≥ {{ data.min_score }}</div>
      </div>
      <div class="card kpi">
        <div class="kpi-label">今日输出</div>
        <div class="kpi-value">{{ data.items.length }}</div>
        <div class="kpi-sub">上限 10，不补足</div>
      </div>
    </div>

    <div class="card" style="margin-bottom: 14px">
      <div class="card-body tight">
        <div class="pill-row">
          <span
            v-for="b in bucketList"
            :key="b.key"
            class="tag"
            :class="b.key === 'CANDIDATE' ? 'tag-down' : 'tag-neutral'"
          >
            {{ b.label }} {{ b.value }}
          </span>
        </div>
        <div class="small" style="margin-top: 8px" :class="data.items.length ? 'muted' : 'strong'">
          {{ data.message }}
        </div>
        <div v-if="data.blocking?.length" class="small" style="margin-top: 6px; color: var(--warn)">
          <div v-for="(reason, i) in data.blocking" :key="i">· {{ reason }}</div>
        </div>
      </div>
    </div>

    <div v-if="!data.items.length" class="card">
      <div class="empty" style="padding: 40px; text-align: center">
        <div class="strong">今日无候选</div>
        <div class="small muted" style="margin-top: 6px">
          上方原因说明了为什么没有合格标的。这是正常结果，不是系统故障。
        </div>
      </div>
    </div>

    <div v-for="card in data.items" :key="card.code" class="card" style="margin-bottom: 12px">
      <div class="card-head" style="cursor: pointer" @click="expanded = expanded === card.code ? '' : card.code">
        <div>
          <div class="card-title">{{ card.title }}</div>
          <div class="small muted" style="margin-top: 4px">
            {{ card.code }} · {{ card.category_label || card.assets.category }} ·
            <span v-if="card.first_seen">首次发现 {{ card.first_seen }}</span>
            <span v-else>首次进入雷达</span>
          </div>
        </div>
        <div class="pill-row" style="align-items: center">
          <span class="tag" :class="actionClass(card.action)">{{ card.action }}</span>
          <span class="num strong" style="font-size: 18px">{{ card.score }}</span>
        </div>
      </div>

      <div class="card-body tight">
        <div class="grid radar-metrics">
          <div><div class="tiny muted">距 MA20</div><div class="num">{{ pct(card.change.dev20) }}</div></div>
          <div><div class="tiny muted">60 日动量</div><div class="num">{{ pct(card.change.ret60) }}</div></div>
          <div><div class="tiny muted">年化波动</div><div class="num">{{ pct(card.change.vol20) }}</div></div>
          <div><div class="tiny muted">质量分</div><div class="num">{{ card.quality_score ?? '—' }}</div></div>
        </div>

        <div style="margin-top: 10px">
          <div class="small"><span class="muted">主要逻辑：</span>{{ card.logic }}</div>
          <div class="small" style="margin-top: 4px">
            <span class="muted">失效条件：</span>{{ card.invalidation }}
          </div>
        </div>

        <template v-if="expanded === card.code">
          <div class="grid radar-detail" style="margin-top: 12px">
            <div>
              <div class="strong small" style="margin-bottom: 4px">潜在上涨驱动</div>
              <div v-for="(d, i) in card.drivers" :key="i" class="small">· {{ d }}</div>
              <div class="strong small" style="margin: 10px 0 4px">潜在下跌风险</div>
              <div v-for="(r, i) in card.risks" :key="i" class="small">· {{ r }}</div>
            </div>
            <div>
              <div class="strong small" style="margin-bottom: 4px">相反证据</div>
              <div v-for="(c, i) in card.counter_evidence" :key="i" class="small">· {{ c }}</div>
              <div class="strong small" style="margin: 10px 0 4px">是否已提前反应</div>
              <div class="small">{{ card.priced_in }}</div>
            </div>
          </div>

          <div class="grid radar-detail" style="margin-top: 12px">
            <div>
              <div class="strong small" style="margin-bottom: 4px">传导机制</div>
              <div class="small">{{ card.transmission }}</div>
              <div class="strong small" style="margin: 10px 0 4px">影响期限</div>
              <div class="small">{{ card.horizon }}</div>
            </div>
            <div>
              <div class="strong small" style="margin-bottom: 4px">关注触发条件</div>
              <div v-for="(t, i) in card.watch_triggers" :key="i" class="small">· {{ t }}</div>
              <div class="strong small" style="margin: 10px 0 4px">建议权重区间</div>
              <div class="small">
                {{ card.weight_range.range || '不给建议' }} — {{ card.weight_range.note }}
              </div>
            </div>
          </div>

          <div class="small muted" style="margin-top: 12px; border-top: 1px solid var(--line); padding-top: 8px">
            <div>相比上期：{{ card.delta.note || `分数 ${card.delta.prev_score} → ${card.score}（${card.delta.score_change > 0 ? '+' : ''}${card.delta.score_change}）` }}</div>
            <div>证据：{{ card.evidence.note }}（{{ card.evidence.bars }} 根日线）</div>
            <div>更新于 {{ card.updated_at }}</div>
          </div>
        </template>
        <div v-else class="tiny muted" style="margin-top: 8px">点击展开完整研究卡片（20 项）</div>
      </div>
    </div>
  </template>
</template>

<style scoped>
/* 桌面：指标一行 4 个、详情两栏；小屏由 main.css 的媒体查询覆盖为 2 列/单列 */
.radar-metrics { grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
.radar-detail { grid-template-columns: 1fr 1fr; gap: 16px; }
</style>

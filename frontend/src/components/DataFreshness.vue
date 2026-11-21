<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/api/endpoints'

const emit = defineEmits<{ (e: 'refreshed'): void }>()

const layers = ref<any[]>([])
const running = ref<any[]>([])
const scope = ref('all')
const busy = ref(false)
const error = ref('')
const notice = ref('')
let timer: number | null = null

const scopes = [
  { value: 'all', label: '全部（行情 → 候选池 → 质量分 → 雷达）' },
  { value: 'radar', label: '仅重算雷达（用现有数据）' },
  { value: 'quotes', label: '仅行情快照' },
  { value: 'universe', label: '仅候选池（约 5 分钟）' },
  { value: 'quality', label: '仅候选池日线与质量分（约 3 分钟）' },
]

const staleCount = computed(() => layers.value.filter((l) => l.stale).length)
const isRunning = computed(() => running.value.length > 0)

function ago(minutes: number | null) {
  if (minutes == null) return '从未'
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${Math.round(minutes)} 分钟前`
  const hours = minutes / 60
  if (hours < 24) return `${hours.toFixed(1)} 小时前`
  return `${(hours / 24).toFixed(1)} 天前`
}

async function load() {
  try {
    const data = await api.refreshStatus()
    layers.value = data.layers || []
    running.value = data.running || []
  } catch (err: any) {
    error.value = err?.message || '读取数据状态失败'
  }
}

async function trigger() {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    const res = await api.refreshData(scope.value)
    notice.value = res.message || '已提交'
    startPolling()
  } catch (err: any) {
    error.value = err?.message || '触发失败'
  } finally {
    busy.value = false
  }
}

function startPolling() {
  stopPolling()
  timer = window.setInterval(async () => {
    await load()
    if (!isRunning.value) {
      stopPolling()
      notice.value = '数据已更新'
      emit('refreshed')
    }
  }, 2500)
}

function stopPolling() {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
}

onMounted(load)
onUnmounted(stopPolling)
</script>

<template>
  <div class="card" style="margin-bottom: 14px">
    <div class="card-body tight">
      <div class="toolbar">
        <span v-for="layer in layers" :key="layer.key" class="fresh-item" :class="{ stale: layer.stale }">
          <i class="fresh-dot" />
          {{ layer.label }}
          <span class="muted tiny">{{ ago(layer.age_minutes) }}</span>
        </span>
        <div class="spacer" />
        <select v-model="scope" class="select" :disabled="isRunning">
          <option v-for="s in scopes" :key="s.value" :value="s.value">{{ s.label }}</option>
        </select>
        <button class="btn sm primary" :disabled="busy || isRunning" @click="trigger">
          {{ isRunning ? '刷新中…' : '更新数据' }}
        </button>
      </div>
      <div v-if="isRunning || notice || error" class="small" style="margin-top: 8px">
        <span v-if="isRunning" style="color: var(--warn)">
          正在执行：{{ running.map((r) => r.label).join('、') }}（可继续浏览，完成后自动刷新）
        </span>
        <span v-else-if="error" style="color: var(--danger)">{{ error }}</span>
        <span v-else style="color: var(--down)">{{ notice }}</span>
      </div>
      <div v-if="staleCount && !isRunning" class="tiny muted" style="margin-top: 6px">
        有 {{ staleCount }} 层数据已过期（红色标记）。雷达用过期数据算出来的分数看起来是新的，实际不是。
      </div>
    </div>
  </div>
</template>

<style scoped>
.fresh-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-2);
  padding: 3px 8px;
  border-radius: 999px;
  background: #f4f6fa;
}
.fresh-item.stale {
  background: var(--warn-soft);
  color: var(--warn);
}
.fresh-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--down);
  display: inline-block;
}
.fresh-item.stale .fresh-dot {
  background: var(--warn);
}
</style>

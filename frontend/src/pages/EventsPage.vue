<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '@/api/endpoints'
import SectionBlock from '@/components/SectionBlock.vue'
import { directionClass, directionLabel, fmtDate, importanceClass, relativeTime } from '@/utils/format'

const route = useRoute()
const tab = ref<'events' | 'news'>('events')
const categories = ref<any[]>([])
const events = ref<any[]>([])
const news = ref<any[]>([])
const meta = ref<any>({ total: 0, pages: 1 })
const loading = ref(false)
const error = ref('')
const page = ref(1)
const filters = ref({
  category: '', min_importance: 0, direction: '', engine: '', days: 7, q: '',
})
const detail = ref<any>(null)
const detailLoading = ref(false)
const detailError = ref('')

const newsFilters = ref({ q: '', days: 7 })
const newsMeta = ref<any>({ total: 0, pages: 1 })
const newsPage = ref(1)

async function loadEvents() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.events({
      category: filters.value.category,
      min_importance: filters.value.min_importance || undefined,
      direction: filters.value.direction,
      engine: filters.value.engine,
      days: filters.value.days,
      q: filters.value.q,
      page: page.value,
      page_size: 20,
    })
    events.value = res.items
    meta.value = res
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadNews() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.news({
      q: newsFilters.value.q,
      days: newsFilters.value.days,
      page: newsPage.value,
      page_size: 20,
    })
    news.value = res.items
    newsMeta.value = res
  } catch (err: any) {
    error.value = err?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function openDetail(id: number) {
  detailLoading.value = true
  detailError.value = ''
  detail.value = null
  try {
    detail.value = await api.event(id)
  } catch (err: any) {
    detailError.value = err?.message || '加载失败'
  } finally {
    detailLoading.value = false
  }
}

onMounted(async () => {
  try {
    const cats = await api.eventCategories()
    categories.value = cats.items.filter((c: any) => c.count > 0 || true)
  } catch { /* 忽略分类加载失败 */ }
  await loadEvents()
  const id = Number(route.query.id)
  if (id) openDetail(id)
})

watch(() => filters.value, () => { page.value = 1; if (tab.value === 'events') loadEvents() }, { deep: true })
watch(page, () => { if (tab.value === 'events') loadEvents() })
watch(newsPage, loadNews)
watch(tab, (value) => {
  if (value === 'news' && !news.value.length) loadNews()
})

const directionOptions = [
  { value: '', label: '全部方向' },
  { value: 'bullish', label: '偏多' },
  { value: 'bearish', label: '偏空' },
  { value: 'mixed', label: '方向冲突' },
  { value: 'unknown', label: '方向未知' },
]

const event = computed(() => detail.value?.event)
const evidence = computed(() => detail.value?.evidence || [])
const impacts = computed(() => detail.value?.impacts || [])
const versions = computed(() => detail.value?.versions || [])
</script>

<template>
  <div class="section-head">
    <div>
      <h2>新闻与事件中心</h2>
      <div class="section-desc">
        同一事件被多家媒体转载时只做一次综合分析；原始来源逐条保留，结论带版本记录。
      </div>
    </div>
    <div class="tabs">
      <div class="tab" :class="{ active: tab === 'events' }" @click="tab = 'events'">事件簇</div>
      <div class="tab" :class="{ active: tab === 'news' }" @click="tab = 'news'">原始新闻</div>
    </div>
  </div>

  <div v-if="tab === 'events'" class="card" style="margin-bottom: 14px">
    <div class="card-body tight">
      <div class="toolbar">
        <input v-model="filters.q" class="input wide" placeholder="搜索标题或摘要" @keyup.enter="page = 1; loadEvents()" />
        <select v-model="filters.category" class="select">
          <option value="">全部类别</option>
          <option v-for="c in categories" :key="c.code" :value="c.code">{{ c.name }}</option>
        </select>
        <select v-model.number="filters.min_importance" class="select">
          <option :value="0">全部重要性</option>
          <option :value="45">重要性 ≥ 45</option>
          <option :value="60">重要性 ≥ 60</option>
          <option :value="72">重要性 ≥ 72（通知级）</option>
          <option :value="85">重要性 ≥ 85</option>
        </select>
        <select v-model="filters.direction" class="select">
          <option v-for="d in directionOptions" :key="d.value" :value="d.value">{{ d.label }}</option>
        </select>
        <select v-model.number="filters.days" class="select">
          <option :value="1">近 1 天</option>
          <option :value="3">近 3 天</option>
          <option :value="7">近 7 天</option>
          <option :value="30">近 30 天</option>
        </select>
        <div class="spacer" />
        <button class="btn sm" @click="loadEvents">刷新</button>
      </div>
    </div>
  </div>

  <div v-else class="card" style="margin-bottom: 14px">
    <div class="card-body tight">
      <div class="toolbar">
        <input v-model="newsFilters.q" class="input wide" placeholder="搜索新闻" @keyup.enter="newsPage = 1; loadNews()" />
        <select v-model.number="newsFilters.days" class="select">
          <option :value="1">近 1 天</option>
          <option :value="3">近 3 天</option>
          <option :value="7">近 7 天</option>
          <option :value="30">近 30 天</option>
        </select>
        <div class="spacer" />
        <span class="small muted">{{ newsMeta.total }} 条原始新闻（已按标题/内容哈希去重）</span>
        <button class="btn sm" @click="loadNews">刷新</button>
      </div>
    </div>
  </div>

  <div v-if="loading" class="loading"><span class="spin" /> 加载中…</div>
  <div v-else-if="error" class="error-box">{{ error }}</div>

  <div v-else-if="tab === 'events'" class="card">
    <div class="table-wrap">
      <table class="table">
        <thead>
          <tr>
            <th style="width: 64px">重要性</th><th>事件</th><th>类别</th>
            <th class="right">来源</th><th>方向</th><th class="right">置信度</th><th>引擎</th><th>发布时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in events" :key="e.id" class="clickable" @click="openDetail(e.id)">
            <td><span class="tag" :class="importanceClass(e.importance)">{{ e.importance }}</span></td>
            <td style="max-width: 460px">
              <div class="wrap">{{ e.title }}</div>
              <div class="tiny muted">{{ e.what_changed }}</div>
            </td>
            <td class="nowrap">{{ e.category_label }}</td>
            <td class="right num">{{ e.source_count }}</td>
            <td><span class="tag" :class="directionClass(e.direction)">{{ directionLabel(e.direction) }}</span></td>
            <td class="right num">{{ (e.confidence * 100).toFixed(0) }}%</td>
            <td><span class="tag" :class="e.analysis_engine === 'rule' ? 'tag-neutral' : 'tag-primary'">{{ e.analysis_engine === 'rule' ? '规则' : 'AI' }}</span></td>
            <td class="muted nowrap">{{ relativeTime(e.published_at) }}</td>
          </tr>
          <tr v-if="!events.length">
            <td colspan="8" class="empty">没有符合条件的事件。请先在「数据源管理」触发新闻采集，系统会自动去重并聚类。</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="meta.pages > 1" class="card-foot toolbar" style="justify-content: center">
      <button class="btn sm" :disabled="page <= 1" @click="page -= 1">上一页</button>
      <span class="small muted">第 {{ page }} / {{ meta.pages }} 页 · 共 {{ meta.total }} 条</span>
      <button class="btn sm" :disabled="page >= meta.pages" @click="page += 1">下一页</button>
    </div>
  </div>

  <div v-else class="card">
    <ul class="list-clean" style="padding: 0 14px">
      <li v-for="n in news" :key="n.id">
        <div style="display: flex; justify-content: space-between; gap: 12px">
          <div style="min-width: 0">
            <a :href="n.url" target="_blank" rel="noopener noreferrer" class="wrap">{{ n.title }}</a>
            <div class="tiny muted">
              {{ n.media }} · {{ fmtDate(n.published_at || n.fetched_at, true) }} · 采集 {{ fmtDate(n.fetched_at, true) }}
              <span v-if="n.event_id"> · <a @click="openDetail(n.event_id)" style="cursor: pointer">已归入事件 #{{ n.event_id }}</a></span>
            </div>
            <div v-if="n.summary" class="small muted wrap" style="margin-top: 2px">{{ n.summary.slice(0, 160) }}</div>
          </div>
          <span class="tag tag-muted nowrap">{{ n.reliability === 'high' ? '高可靠' : n.reliability === 'low' ? '低可靠' : '中等' }}</span>
        </div>
      </li>
      <li v-if="!news.length" class="empty">暂无新闻数据</li>
    </ul>
    <div v-if="newsMeta.pages > 1" class="card-foot toolbar" style="justify-content: center">
      <button class="btn sm" :disabled="newsPage <= 1" @click="newsPage -= 1">上一页</button>
      <span class="small muted">第 {{ newsPage }} / {{ newsMeta.pages }} 页</span>
      <button class="btn sm" :disabled="newsPage >= newsMeta.pages" @click="newsPage += 1">下一页</button>
    </div>
  </div>

  <!-- 事件详情 -->
  <div v-if="detail || detailLoading || detailError" class="drawer-mask" @click.self="detail = null; detailError = ''">
    <div class="drawer">
      <div class="drawer-head">
        <div>
          <div class="tiny muted" style="margin-bottom: 4px">事件详情</div>
          <h2 style="line-height: 1.5">{{ event?.title || '加载中…' }}</h2>
        </div>
        <button class="btn ghost" @click="detail = null; detailError = ''">关闭</button>
      </div>
      <div class="drawer-body">
        <div v-if="detailLoading" class="loading"><span class="spin" /> 读取证据链…</div>
        <div v-else-if="detailError" class="error-box">{{ detailError }}</div>
        <template v-else-if="event">
          <div class="pill-row" style="margin-bottom: 12px">
            <span class="tag" :class="importanceClass(event.importance)">重要性 {{ event.importance }}</span>
            <span class="tag" :class="directionClass(event.direction)">{{ directionLabel(event.direction) }}</span>
            <span class="tag tag-neutral">{{ event.category_label }}</span>
            <span class="tag tag-neutral">置信度 {{ (event.confidence * 100).toFixed(0) }}%</span>
            <span class="tag tag-neutral">{{ event.source_count }} 个来源</span>
            <span class="tag tag-neutral">{{ event.analysis_engine === 'rule' ? '规则引擎' : 'AI 分析' }}</span>
            <span class="tag tag-muted">版本 v{{ event.analysis_version }}</span>
          </div>

          <div class="banner info" style="margin-bottom: 12px">
            发布时间 {{ fmtDate(event.published_at, true) }} · 采集 {{ fmtDate(event.collected_at, true) }} ·
            方向判断允许为未知或存在冲突，置信度仅表示证据质量。
          </div>

          <SectionBlock title="实际发生了什么">
            <p class="small">{{ event.what_happened || event.summary }}</p>
            <p v-if="event.analysis?.what_confirmed" class="small muted">已证实来源：{{ event.analysis.what_confirmed }}</p>
          </SectionBlock>

          <SectionBlock title="相对市场认知的变化">
            <p class="small">{{ event.what_changed || '—' }}</p>
          </SectionBlock>

          <SectionBlock title="仍不确定的部分">
            <p class="small">{{ event.what_uncertain || '—' }}</p>
          </SectionBlock>

          <SectionBlock title="影响传导路径">
            <ol v-if="event.transmission?.length" class="small" style="padding-left: 18px; margin: 0">
              <li v-for="(t, i) in event.transmission" :key="i" style="margin-bottom: 4px">
                {{ t.from }} → {{ t.to }}：<span class="muted">{{ t.mechanism || t.detail }}</span>
              </li>
            </ol>
            <p v-else class="small muted">—</p>
          </SectionBlock>

          <SectionBlock title="受影响的资产">
            <table v-if="impacts.length" class="table">
              <thead><tr><th>品种</th><th>类别</th><th>方向</th><th>期限</th><th>传导依据</th></tr></thead>
              <tbody>
                <tr v-for="i in impacts" :key="i.symbol">
                  <td><router-link :to="`/assets/${encodeURIComponent(i.symbol)}`">{{ i.name }}</router-link></td>
                  <td class="small">{{ i.class }}</td>
                  <td><span class="tag" :class="directionClass(i.direction)">{{ directionLabel(i.direction) }}</span></td>
                  <td class="small">{{ i.horizon === 'short' ? '短期' : i.horizon === 'mid' ? '中期' : i.horizon === 'long' ? '长期' : '未知' }}</td>
                  <td class="small muted wrap">{{ i.mechanism }}</td>
                </tr>
              </tbody>
            </table>
            <p v-else class="small muted">未识别到明确的受影响资产。</p>
          </SectionBlock>

          <SectionBlock title="反面证据与失效条件">
            <ul v-if="event.counter_evidence?.length" class="small" style="padding-left: 18px; margin: 0">
              <li v-for="(c, i) in event.counter_evidence" :key="i">{{ c }}</li>
            </ul>
            <p class="small" style="margin-top: 6px">失效条件：{{ event.invalidation || '—' }}</p>
          </SectionBlock>

          <SectionBlock title="原始证据">
            <ul class="list-clean">
              <li v-for="e in evidence" :key="e.id">
                <a :href="e.url" target="_blank" rel="noopener noreferrer" class="wrap">{{ e.title || e.quote?.slice(0, 60) }}</a>
                <div class="tiny muted">{{ e.source_name }} · {{ fmtDate(e.published_at, true) }} · 可靠度 {{ e.reliability }}</div>
              </li>
              <li v-if="!evidence.length" class="small muted">无证据</li>
            </ul>
          </SectionBlock>

          <SectionBlock v-if="versions.length" title="结论版本记录">
            <ul class="list-clean">
              <li v-for="v in versions" :key="v.version" class="small">
                v{{ v.version }} · {{ v.engine }} · {{ v.direction }} · 置信度 {{ (v.confidence * 100).toFixed(0) }}%
                <div class="tiny muted">{{ fmtDate(v.created_at, true) }} · {{ v.change_reason }}</div>
              </li>
            </ul>
          </SectionBlock>

          <div class="banner" style="margin-top: 12px">{{ detail.disclaimer }}</div>
        </template>
      </div>
    </div>
  </div>
</template>

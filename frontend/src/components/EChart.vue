<script setup lang="ts">
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{
  option: Record<string, any>
  height?: string
  empty?: boolean
}>()

const el = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let observer: ResizeObserver | null = null

function render() {
  if (!el.value) return
  if (!chart) {
    chart = echarts.init(el.value, undefined, { renderer: 'canvas' })
  }
  const base: Record<string, any> = {
    textStyle: { fontFamily: 'inherit', color: '#3d4a5c', fontSize: 12 },
    grid: { left: 8, right: 14, top: 24, bottom: 6, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(255,255,255,0.98)',
      borderColor: '#e3e8f0',
      borderWidth: 1,
      textStyle: { color: '#17202f', fontSize: 12 },
      axisPointer: { lineStyle: { color: '#cfd8e6' } },
    },
  }
  chart.setOption({ ...base, ...props.option }, true)
}

onMounted(() => {
  render()
  if (el.value) {
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(el.value)
  }
})

watch(
  () => props.option,
  () => render(),
  { deep: true },
)

onBeforeUnmount(() => {
  observer?.disconnect()
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="el" class="chart" :style="{ height: height || undefined }" />
</template>

<template>
  <div class="chart-box">
    <div class="chart-box__head">
      <div class="chart-box__title">{{ title }}</div>
      <div class="chart-box__extra">
        <slot name="extra">{{ extra }}</slot>
      </div>
    </div>
    <div ref="el" class="chart-box__canvas" :style="{ height: height + 'px' }"></div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  title: { type: String, default: '' },
  extra: { type: String, default: '' },
  option: { type: Object, default: () => ({}) },
  height: { type: Number, default: 300 }
})

const el = ref(null)
let chart = null
let ro = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  chart.setOption(props.option || {}, true)
  chart.resize()
}

function onResize() {
  if (chart) chart.resize()
}

onMounted(async () => {
  await nextTick()
  render()
  window.addEventListener('resize', onResize)
  if (typeof ResizeObserver !== 'undefined' && el.value) {
    ro = new ResizeObserver(() => onResize())
    ro.observe(el.value)
  }
})

watch(
  () => props.option,
  async () => {
    await nextTick()
    render()
  },
  { deep: true }
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (ro) {
    ro.disconnect()
    ro = null
  }
  if (chart) {
    chart.dispose()
    chart = null
  }
})
</script>

<style scoped>
.chart-box {
  background: #ffffff;
  border-radius: 12px;
  padding: 16px 18px 12px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 1px 3px rgba(15, 23, 42, 0.04);
  min-width: 0;
}

.chart-box__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}

.chart-box__title {
  font-size: 15px;
  font-weight: 600;
  color: #111827;
}

.chart-box__extra {
  font-size: 12px;
  color: #9ca3af;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chart-box__canvas {
  width: 100%;
}
</style>

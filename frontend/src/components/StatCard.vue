<template>
  <div class="stat-card">
    <div class="stat-card__title">{{ title }}</div>
    <div class="stat-card__value" :style="valueStyle">{{ value }}</div>
    <div class="stat-card__sub">{{ sub }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  title: { type: String, default: '' },
  value: { type: [String, Number], default: '—' },
  sub: { type: String, default: '' },
  color: { type: String, default: '' }
})

const valueStyle = computed(() => (props.color ? { color: props.color } : {}))
</script>

<style scoped>
.stat-card {
  background: #ffffff;
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 1px 3px rgba(15, 23, 42, 0.04);
  min-width: 0;
}

.stat-card__title {
  font-size: 13px;
  color: #6b7280;
  line-height: 20px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stat-card__value {
  margin-top: 6px;
  font-size: 26px;
  line-height: 34px;
  font-weight: 700;
  color: #111827;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stat-card__sub {
  margin-top: 4px;
  font-size: 12px;
  color: #9ca3af;
  line-height: 18px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>

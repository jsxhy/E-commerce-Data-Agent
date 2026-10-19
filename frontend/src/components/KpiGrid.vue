<template>
  <div class="kpi-grid" :style="{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }">
    <slot />
  </div>
</template>

<script setup>
defineProps({
  cols: { type: Number, default: 4 }
})
</script>

<style scoped>
.kpi-grid {
  display: grid;
  gap: 16px;
  margin-bottom: 16px;
}

@media (max-width: 1200px) {
  .kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  }
}

@media (max-width: 720px) {
  .kpi-grid {
    grid-template-columns: minmax(0, 1fr) !important;
  }
}
</style>

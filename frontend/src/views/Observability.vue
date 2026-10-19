<template>
  <div class="observability">
    <PageHeader title="Agent 运行观测" desc="从运行记录里实时统计，反映 Agent 的稳定性、效率与用户反馈。">
      <div class="obs-actions">
        <el-switch v-model="onlyMine" active-text="只看我的" @change="load" />
        <span class="obs-time">更新时间 {{ data.updated_at || '—' }}</span>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </PageHeader>

    <KpiGrid :cols="4">
      <StatCard
        v-for="card in kpiCards"
        :key="card.key"
        :title="card.title"
        :value="card.value"
        :sub="card.sub"
        :color="card.color"
      />
    </KpiGrid>

    <div class="obs-charts">
      <ChartBox title="路由分布" :extra="routeExtra" :option="routeOption" :height="300" />
      <ChartBox
        title="各阶段平均耗时"
        extra="例序取前 8，按耗时降序"
        :option="stageOption"
        :height="300"
      />
    </div>

    <ChartBox title="近 14 天运行量" :extra="dailyExtra" :option="dailyOption" :height="280" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api/index.js'
import PageHeader from '../components/PageHeader.vue'
import StatCard from '../components/StatCard.vue'
import KpiGrid from '../components/KpiGrid.vue'
import ChartBox from '../components/ChartBox.vue'

const loading = ref(false)
const onlyMine = ref(false)
const data = ref({
  updated_at: '',
  kpis: {},
  route_dist: [],
  stage_avg: [],
  daily_runs: { total: 0, points: [] }
})

/** 契约里 KPI 统一为 { value, label, unit? }；兼容少量别名键。 */
function kpi(key, alias) {
  const k = data.value.kpis || {}
  return k[key] || (alias ? k[alias] : null) || { value: 0, label: '' }
}

const kpiCards = computed(() => {
  const likeRate = kpi('like_rate')
  // 契约只给 label「赞1 / 踩0」；若后端额外给 like_count/dislike_count 则优先用它拼。
  const likeCount = likeRate.like_count ?? likeRate.likes
  const dislikeCount = likeRate.dislike_count ?? likeRate.dislikes
  const likeSub =
    likeCount != null && dislikeCount != null
      ? `赞${likeCount}/踩${dislikeCount}`
      : String(likeRate.label || '赞0/踩0').replace(/\s*\/\s*/g, '/')
  return [
    {
      key: 'total_runs',
      title: '总运行次数',
      value: String(kpi('total_runs').value ?? 0),
      sub: '含进行中与已取消'
    },
    { key: 'success_runs', title: '成功', value: String(kpi('success_runs').value ?? 0), sub: '正常跑完' },
    {
      key: 'failed_runs',
      title: '失败',
      value: String(kpi('failed_runs').value ?? 0),
      sub: '异常终止',
      color: '#dc2626'
    },
    {
      key: 'cancelled_runs',
      title: '已取消',
      value: String(kpi('cancelled_runs').value ?? 0),
      sub: '用户放弃或中断'
    },
    {
      key: 'unfinished_runs',
      title: '未完成',
      value: String(kpi('unfinished_runs', 'pending_runs').value ?? 0),
      sub: '进行中/待确认',
      color: '#ea580c'
    },
    {
      key: 'success_rate',
      title: '成功率',
      value: formatPercent(kpi('success_rate').value),
      sub: '已结束的运行里成功的比例'
    },
    {
      key: 'avg_duration',
      title: '平均耗时',
      value: formatDurationKpi(kpi('avg_duration', 'avg_duration_ms')),
      sub: '仅统计成功运行'
    },
    {
      key: 'like_rate',
      title: '点赞率',
      value: formatPercent(likeRate.value),
      sub: likeSub
    }
  ]
})

function formatPercent(value) {
  const n = Number(value || 0)
  return `${Number.isInteger(n) ? n : n.toFixed(1)}%`
}

/** 阶段耗时：毫秒，≥1000ms 显示 11.4s，否则 469ms。 */
function formatDuration(value) {
  const n = Number(value || 0)
  if (n >= 1000) return `${(n / 1000).toFixed(1)}s`
  return `${Math.round(n)}ms`
}

/**
 * KPI 里的平均耗时：契约给 { value: 21.8, unit: 's' }（秒），
 * 兼容误传毫秒（value 很大或 unit==='ms'）的情况。
 */
function formatDurationKpi(item) {
  const value = Number(item && item.value) || 0
  const unit = (item && item.unit) || ''
  if (unit === 's' || (unit !== 'ms' && value < 600)) {
    return `${Number.isInteger(value) ? value : value.toFixed(1)} s`
  }
  return `${(value / 1000).toFixed(1)} s`
}

const routeExtra = computed(() => {
  const total = (data.value.route_dist || []).reduce((sum, item) => sum + Number(item.count || 0), 0)
  return `共 ${total} 次`
})

const routeOption = computed(() => {
  const list = data.value.route_dist || []
  const total = list.reduce((sum, item) => sum + Number(item.count || 0), 0)
  return {
    tooltip: { trigger: 'item', formatter: '{b}：{c} 次（{d}%）' },
    legend: {
      bottom: 0,
      icon: 'circle',
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { color: '#6b7280', fontSize: 12 }
    },
    color: ['#1d4ed8', '#22c55e', '#f59e0b', '#8b5cf6', '#ef4444'],
    series: [
      {
        type: 'pie',
        radius: ['54%', '74%'],
        center: ['50%', '44%'],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: '#ffffff', borderWidth: 2 },
        label: { show: false },
        labelLine: { show: false },
        data: list.map((item) => ({ name: item.name || item.route, value: Number(item.count || 0) }))
      }
    ],
    graphic: [
      {
        type: 'text',
        left: 'center',
        top: '36%',
        style: {
          text: String(total),
          fontSize: 26,
          fontWeight: 700,
          fill: '#111827',
          textAlign: 'center'
        }
      },
      {
        type: 'text',
        left: 'center',
        top: '48%',
        style: {
          text: '总次数',
          fontSize: 12,
          fill: '#9ca3af',
          textAlign: 'center'
        }
      }
    ]
  }
})

const stageOption = computed(() => {
  const list = (data.value.stage_avg || [])
    .slice(0, 8)
    .map((item) => ({ name: item.stage, value: Number(item.avg_ms || 0) }))
    .sort((a, b) => a.value - b.value)
  return {
    grid: { left: 8, right: 56, top: 12, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: formatDuration },
    xAxis: { type: 'value', show: false },
    yAxis: {
      type: 'category',
      data: list.map((item) => item.name),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#4b5563', fontSize: 12 }
    },
    series: [
      {
        type: 'bar',
        data: list.map((item) => item.value),
        barWidth: '52%',
        itemStyle: { color: '#60a5fa', borderRadius: [0, 6, 6, 0] },
        label: {
          show: true,
          position: 'right',
          color: '#6b7280',
          fontSize: 12,
          formatter: (params) => formatDuration(params.value)
        }
      }
    ]
  }
})

const dailyExtra = computed(() => `共 ${(data.value.daily_runs && data.value.daily_runs.total) || 0} 次`)

const dailyOption = computed(() => {
  const points = (data.value.daily_runs && data.value.daily_runs.points) || []
  return {
    grid: { left: 8, right: 16, top: 20, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', valueFormatter: (v) => `${v} 次` },
    xAxis: {
      type: 'category',
      data: points.map((p) => String(p.date || '').slice(5)),
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisTick: { show: false },
      axisLabel: { color: '#9ca3af', fontSize: 11 }
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#9ca3af', fontSize: 11 }
    },
    series: [
      {
        type: 'bar',
        name: '运行量',
        data: points.map((p) => Number(p.count || 0)),
        barWidth: '48%',
        itemStyle: { color: '#1d4ed8', borderRadius: [6, 6, 0, 0] }
      }
    ]
  }
})

async function load() {
  loading.value = true
  try {
    // 契约参数名是 only_mine；同时带上 mine=1 兼容另一种约定（后端会忽略多余参数）。
    const params = onlyMine.value ? { only_mine: 1, mine: 1 } : {}
    const res = await api.get('/observability/overview', params)
    data.value = { ...data.value, ...(res || {}) }
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.observability {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.obs-actions {
  display: flex;
  align-items: center;
  gap: 14px;
}

.obs-time {
  font-size: 12px;
  color: #9ca3af;
  white-space: nowrap;
}

.obs-charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

@media (max-width: 1200px) {
  .obs-charts {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>

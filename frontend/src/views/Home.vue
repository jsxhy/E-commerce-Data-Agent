<template>
  <div class="home">
    <!-- 问候卡 -->
    <section class="greet app-card">
      <div class="greet__left">
        <h2 class="greet__title">你好，管理员</h2>
        <p class="greet__desc">
          这里是电商数据分析 Agent 的运营总览，可以一键巡检并查看最新经营与风险数据。
        </p>
      </div>
      <div class="greet__right">
        <span class="greet__time">更新时间 {{ overview.updated_at || '—' }}</span>
        <el-button type="primary" :loading="inspecting" @click="onInspect">一键巡检</el-button>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </section>

    <!-- 8 个 KPI -->
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

    <!-- 图表 -->
    <div class="home__charts">
      <ChartBox title="近 30 天订单量" :extra="trendExtra" :option="trendOption" :height="300" />
      <ChartBox title="评分分布" :extra="ratingExtra" :option="ratingOption" :height="300" />
    </div>

    <!-- 需要重点关注的商品 -->
    <section class="app-card attention">
      <div class="attention__head">
        <div class="attention__title">需要重点关注的商品</div>
      </div>
      <div v-if="loading" class="app-empty">加载中…</div>
      <template v-else>
        <div v-if="attention.length === 0" class="app-empty">暂无需要关注的商品</div>
        <ul v-else class="attention__list">
          <li v-for="item in attention" :key="item.product_id" class="attention__item">
            <span class="attention__name">{{ item.name }}</span>
            <el-tag v-if="item.category" size="small" type="info" effect="plain">{{ item.category }}</el-tag>
            <span class="attention__bad">{{ item.bad_review_count }} 条差评</span>
            <span class="attention__rating">平均 {{ Number(item.avg_rating || 0).toFixed(1) }} 分</span>
          </li>
        </ul>
      </template>
    </section>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import StatCard from '../components/StatCard.vue'
import KpiGrid from '../components/KpiGrid.vue'
import ChartBox from '../components/ChartBox.vue'

const loading = ref(false)
const inspecting = ref(false)
const overview = ref({
  updated_at: '',
  greeting: '',
  subtitle: '',
  kpis: {},
  order_trend: { total_orders: 0, total_amount: 0, points: [] },
  rating_dist: { avg: 0, buckets: [] },
  attention_products: []
})

function kpi(key) {
  return (overview.value.kpis && overview.value.kpis[key]) || { value: 0, label: '' }
}

function formatMoney(value) {
  const n = Number(value || 0)
  return (
    '¥' +
    n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  )
}

const kpiCards = computed(() => [
  { key: 'product_count', title: '商品数', value: String(kpi('product_count').value ?? 0), sub: '全部商品' },
  { key: 'order_count', title: '订单数', value: String(kpi('order_count').value ?? 0), sub: '累计订单' },
  { key: 'review_count', title: '评价数', value: String(kpi('review_count').value ?? 0), sub: '累计评价' },
  {
    key: 'avg_rating',
    title: '平均评分',
    value: Number(kpi('avg_rating').value || 0).toFixed(2),
    sub: '满分 5 分'
  },
  {
    key: 'paid_amount',
    title: '累计成交',
    value: formatMoney(kpi('paid_amount').value),
    sub: '订单实付合计（不含已取消）'
  },
  {
    key: 'high_risk_reports',
    title: '高风险诊断报告',
    value: String(kpi('high_risk_reports').value ?? 0),
    sub: 'Agent 判定需人工确认',
    color: '#dc2626'
  },
  {
    key: 'unread_alerts',
    title: '未读预警',
    value: String(kpi('unread_alerts').value ?? 0),
    sub: '站内通知',
    color: '#ea580c'
  },
  { key: 'pending_review', title: '待复核商品', value: String(kpi('pending_review').value ?? 0), sub: '人工复核状态' }
])

const attention = computed(() => overview.value.attention_products || [])

const trendExtra = computed(() => {
  const t = overview.value.order_trend || {}
  return `共 ${t.total_orders || 0} 单 · ${formatMoney(t.total_amount)}`
})

const trendOption = computed(() => {
  const points = (overview.value.order_trend && overview.value.order_trend.points) || []
  const dates = points.map((p) => String(p.date || '').slice(5))
  const counts = points.map((p) => Number(p.count || 0))
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', valueFormatter: (v) => `${v} 单` },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: dates,
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisTick: { show: false },
      axisLabel: { color: '#9ca3af', fontSize: 11, interval: Math.max(0, Math.ceil(dates.length / 8) - 1) }
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      splitLine: { lineStyle: { color: '#f1f5f9' } },
      axisLabel: { color: '#9ca3af', fontSize: 11 }
    },
    series: [
      {
        type: 'line',
        name: '订单量',
        data: counts,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2, color: '#1d4ed8' },
        itemStyle: { color: '#1d4ed8' },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(29, 78, 216, 0.26)' },
              { offset: 1, color: 'rgba(29, 78, 216, 0.02)' }
            ]
          }
        }
      }
    ]
  }
})

const ratingExtra = computed(() => `平均 ${Number((overview.value.rating_dist || {}).avg || 0).toFixed(2)} 分`)

const ratingOption = computed(() => {
  const buckets = (overview.value.rating_dist && overview.value.rating_dist.buckets) || []
  const labels = buckets.map((b) => `${b.star} 星`)
  const counts = buckets.map((b) => Number(b.count || 0))
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: { trigger: 'axis', valueFormatter: (v) => `${v} 条` },
    xAxis: {
      type: 'category',
      data: labels,
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
        name: '评价数',
        data: counts,
        barWidth: '46%',
        itemStyle: { color: '#93c5fd', borderRadius: [6, 6, 0, 0] },
        emphasis: { itemStyle: { color: '#1d4ed8' } }
      }
    ]
  }
})

async function load() {
  loading.value = true
  try {
    const data = await api.get('/dashboard/overview')
    overview.value = {
      ...overview.value,
      ...(data || {})
    }
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

async function onInspect() {
  inspecting.value = true
  try {
    const res = await api.post('/dashboard/inspect')
    ElMessage.success((res && res.message) || '巡检完成')
    await load()
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    inspecting.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.home {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.greet {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.greet__title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: #111827;
  line-height: 28px;
}

.greet__desc {
  margin: 6px 0 0;
  font-size: 14px;
  color: #6b7280;
  line-height: 22px;
}

.greet__right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.greet__time {
  font-size: 12px;
  color: #9ca3af;
  white-space: nowrap;
}

.home__charts {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 16px;
}

.attention__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.attention__title {
  font-size: 15px;
  font-weight: 600;
  color: #111827;
}

.attention__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.attention__item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 11px 0;
  border-top: 1px solid #f1f5f9;
  font-size: 14px;
}

.attention__item:first-child {
  border-top: none;
}

.attention__name {
  font-weight: 600;
  color: #111827;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attention__bad {
  color: #dc2626;
  font-size: 13px;
  white-space: nowrap;
}

.attention__rating {
  margin-left: auto;
  color: #6b7280;
  font-size: 13px;
  white-space: nowrap;
}

@media (max-width: 1200px) {
  .home__charts {
    grid-template-columns: minmax(0, 1fr);
  }

  .greet {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>

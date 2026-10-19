<template>
  <div class="page">
    <div class="page-title">运行历史</div>
    <div class="page-desc">
      每一次 Agent 分析的完整轨迹：路由判定、执行步骤、耗时与用户反馈。点击任意一行查看运行详情。
    </div>

    <div class="card">
      <el-table
        v-loading="loading"
        :data="items"
        row-key="id"
        class="clickable-table"
        empty-text="暂无运行记录"
        @row-click="openDetail"
      >
        <el-table-column prop="question" label="问题" min-width="320" show-overflow-tooltip />
        <el-table-column label="路由" width="120">
          <template #default="{ row }">
            <el-tag :type="routeTag(row.route)" size="small" effect="light">{{ routeName(row.route) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag
              size="small"
              effect="dark"
              class="status-tag"
              :style="{ backgroundColor: statusColor(row.status), borderColor: statusColor(row.status) }"
            >
              {{ statusName(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="120">
          <template #default="{ row }">
            <span class="mono">{{ row.duration_ms }} ms</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="180" />
      </el-table>

      <div class="pager">
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="size"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          background
          @current-change="load"
          @size-change="onSizeChange"
        />
      </div>
    </div>

    <el-drawer v-model="drawer" title="运行详情" size="560px" @closed="detail = null">
      <div v-if="detailLoading" v-loading="true" class="detail-loading"></div>
      <div v-else-if="detail" class="detail">
        <div class="detail-q">{{ detail.question }}</div>
        <div class="detail-meta">
          {{ statusName(detail.status) }} · {{ routeName(detail.route) }} · {{ detail.duration_ms }} ms
        </div>

        <div class="detail-section-title">回答摘要</div>
        <div class="detail-answer">{{ answerPreview }}</div>
        <div v-if="answerTruncated" class="detail-note">只保留前 500 字，完整回答见对话页</div>

        <div class="detail-section-title">执行步骤（{{ (detail.steps || []).length }}）</div>
        <el-empty v-if="!(detail.steps || []).length" description="没有步骤记录" :image-size="60" />
        <div v-else class="steps">
          <div v-for="s in detail.steps" :key="s.seq" class="step-row">
            <span class="step-seq">#{{ s.seq }}</span>
            <span class="step-stage">{{ stageName(s.stage) }}</span>
            <span v-if="s.actor" class="step-actor">{{ s.actor }}</span>
            <span class="step-detail">{{ s.detail }}</span>
            <span class="step-ms">{{ s.duration_ms }}ms</span>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../api'

const STAGES = {
  starting: '流程启动',
  scheduling: '调度',
  routing: '意图路由',
  'agent.analysis': '多角色分析',
  querying: '数据查询',
  deepening: '语义深挖',
  preparing_evidence: '证据整合',
  'agent.report': '报告决策',
  deciding: '风险判断',
  action_decision: '动作决策',
  action_pending: '等待确认',
  executing: '动作执行',
  finished: '收尾'
}

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const loading = ref(false)

const drawer = ref(false)
const detail = ref(null)
const detailLoading = ref(false)

function stageName(s) {
  return STAGES[s] || s || '-'
}
function routeName(r) {
  if (r === 'structured') return '结构化'
  if (r === 'semantic') return '语义检索'
  return '未判定'
}
function routeTag(r) {
  if (r === 'structured') return 'primary'
  if (r === 'semantic') return 'success'
  return 'info'
}
function statusName(s) {
  if (s === 'success') return '成功'
  if (s === 'failed') return '失败'
  if (s === 'pending') return '进行中'
  if (s === 'cancelled') return '已取消'
  return s || '-'
}
function statusColor(s) {
  if (s === 'success') return '#16a34a'
  if (s === 'failed') return '#dc2626'
  if (s === 'pending') return '#2563eb'
  return '#9ca3af'
}

const answerPreview = computed(() => {
  const a = (detail.value && detail.value.answer) || ''
  return a.slice(0, 500)
})
const answerTruncated = computed(() => {
  const a = (detail.value && detail.value.answer) || ''
  return a.length > 500
})

async function load() {
  loading.value = true
  try {
    const data = await api.get('/runs', { page: page.value, size: size.value })
    items.value = (data && data.items) || []
    total.value = (data && data.total) || 0
  } catch (e) {
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function onSizeChange() {
  page.value = 1
  load()
}

async function openDetail(row) {
  drawer.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await api.get('/runs/' + row.id)
  } catch (e) {
    detail.value = row
  } finally {
    detailLoading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page-title {
  font-size: 18px;
  font-weight: 700;
  color: #111827;
  line-height: 26px;
}
.page-desc {
  margin: 6px 0 16px;
  font-size: 13px;
  color: #6b7280;
  line-height: 20px;
  max-width: 860px;
}
.card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 1px 3px rgba(15, 23, 42, 0.04);
  padding: 18px 20px;
}
.clickable-table :deep(.el-table__row) {
  cursor: pointer;
}
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  color: #4b5563;
}
.status-tag {
  border: none;
  color: #fff;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}
.detail-loading {
  height: 160px;
}
.detail-q {
  font-size: 15px;
  font-weight: 600;
  color: #111827;
  line-height: 1.6;
}
.detail-meta {
  margin-top: 6px;
  font-size: 12.5px;
  color: #9ca3af;
}
.detail-section-title {
  margin: 20px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: #374151;
}
.detail-answer {
  white-space: pre-wrap;
  font-size: 13.5px;
  line-height: 1.8;
  color: #374151;
  background: #fafbfc;
  border: 1px solid #eef0f4;
  border-radius: 8px;
  padding: 10px 12px;
}
.detail-note {
  margin-top: 6px;
  font-size: 12px;
  color: #9ca3af;
}
.steps {
  border-left: 2px solid #e5e7eb;
  padding-left: 10px;
}
.step-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.9;
  color: #4b5563;
}
.step-seq {
  color: #9ca3af;
  flex: none;
  min-width: 28px;
}
.step-stage {
  color: #111827;
  flex: none;
}
.step-actor {
  color: #6b7280;
  flex: none;
}
.step-detail {
  color: #6b7280;
  flex: 1;
  min-width: 0;
}
.step-ms {
  color: #9ca3af;
  flex: none;
}
</style>

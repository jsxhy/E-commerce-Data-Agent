<template>
  <div class="page">
    <div class="page-title">诊断报告</div>
    <div class="page-desc">
      每一次高风险分析的落库结果：关联商品、风险等级、证据链与已执行动作，并汇总站内预警通知。
    </div>

    <div class="card">
      <div class="toolbar">
        <el-input
          v-model="q"
          placeholder="按问题/摘要/商品搜索"
          clearable
          style="width: 320px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
        <el-button type="primary" @click="onSearch">查询</el-button>
      </div>

      <el-tabs v-model="tab">
        <el-tab-pane label="诊断报告" name="reports">
          <el-table v-loading="loading" :data="items" empty-text="暂无诊断报告">
            <el-table-column prop="question" label="问题" min-width="240" show-overflow-tooltip />
            <el-table-column label="关联商品" width="150" show-overflow-tooltip>
              <template #default="{ row }">{{ row.product_name || '-' }}</template>
            </el-table-column>
            <el-table-column label="风险" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.risk === 'high'" type="danger" size="small" effect="light">高</el-tag>
                <el-tag v-else type="success" size="small" effect="light">低</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="evidence_count" label="证据数" width="90" align="center" />
            <el-table-column prop="summary" label="摘要" min-width="260" show-overflow-tooltip />
            <el-table-column prop="created_at" label="生成时间" width="170" />
            <el-table-column label="操作" width="90" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openReport(row)">详情</el-button>
              </template>
            </el-table-column>
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
        </el-tab-pane>

        <el-tab-pane :label="'预警通知（' + unreadCount + '未读）'" name="notifications">
          <div class="notif-head">
            <span class="notif-hint">共 {{ notifications.length }} 条通知，未读 {{ unreadCount }} 条</span>
            <el-button size="small" :disabled="!unreadCount || markingAll" :loading="markingAll" @click="markAllRead">
              全部标为已读
            </el-button>
          </div>
          <div v-loading="notifLoading">
            <el-empty v-if="!notifications.length && !notifLoading" description="暂无预警通知" />
            <div
              v-for="n in notifications"
              :key="n.id"
              class="notif-item"
              :class="{ unread: !n.read }"
              @click="markRead(n)"
            >
              <span class="dot" :class="{ show: !n.read }"></span>
              <div class="notif-main">
                <div class="notif-title" :class="{ bold: !n.read }">
                  {{ n.title }}
                  <el-tag v-if="n.level === 'high'" type="danger" size="small" effect="light">高风险</el-tag>
                </div>
                <div class="notif-content">{{ n.content }}</div>
                <div class="notif-time">{{ n.created_at }}</div>
              </div>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </div>

    <el-dialog v-model="dialog" title="诊断报告详情" width="720px" top="6vh">
      <div v-if="detailLoading" v-loading="true" class="dialog-loading"></div>
      <div v-else-if="detail" class="detail">
        <div class="d-row">
          <div class="d-label">问题</div>
          <div class="d-value strong">{{ detail.question }}</div>
        </div>
        <div class="d-row">
          <div class="d-label">关联商品</div>
          <div class="d-value">{{ detail.product_name || '-' }}</div>
        </div>
        <div class="d-row">
          <div class="d-label">风险等级</div>
          <div class="d-value">
            <el-tag v-if="detail.risk === 'high'" type="danger" size="small">高</el-tag>
            <el-tag v-else type="success" size="small">低</el-tag>
            <span class="gap">{{ detail.created_at }}</span>
          </div>
        </div>
        <div class="d-row">
          <div class="d-label">摘要</div>
          <div class="d-value pre">{{ detail.summary }}</div>
        </div>

        <div class="d-section">证据链（{{ evidenceList.length }}）</div>
        <el-empty v-if="!evidenceList.length" description="无证据" :image-size="60" />
        <div v-for="(e, i) in evidenceList" :key="i" class="evi">
          <div class="evi-head">
            <el-tag size="small" effect="plain" :type="e.source === 'review' ? 'warning' : 'primary'">
              {{ e.source === 'review' ? '评价' : '商品' }}
            </el-tag>
            <span class="evi-ref">#{{ e.ref_id }}</span>
            <span v-if="e.rating" class="evi-rating">★ {{ e.rating }}</span>
            <span v-if="e.distance != null" class="evi-dist">距离 {{ e.distance }}</span>
          </div>
          <div class="evi-snippet">{{ e.snippet }}</div>
        </div>

        <div class="d-section">动作列表（{{ actionList.length }}）</div>
        <el-empty v-if="!actionList.length" description="无动作" :image-size="60" />
        <div v-for="(a, i) in actionList" :key="i" class="act-row">
          <span class="act-label">{{ actionName(a.type, a.label) }}</span>
          <el-tag size="small" :type="a.status === 'executed' ? 'success' : 'info'" effect="light">
            {{ a.status === 'executed' ? '已执行' : a.status || '-' }}
          </el-tag>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const ACTION_NAMES = {
  save_diagnosis_report: '保存诊断报告',
  send_alert_notification: '发送站内预警',
  mark_product_pending_review: '标记商品待人工复核'
}

const tab = ref('reports')
const q = ref('')

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(10)
const loading = ref(false)

const notifications = ref([])
const unreadCount = ref(0)
const notifLoading = ref(false)
const markingAll = ref(false)

const dialog = ref(false)
const detail = ref(null)
const detailLoading = ref(false)

function actionName(type, fallback) {
  return ACTION_NAMES[type] || fallback || type || '-'
}

function parseMaybeJson(v) {
  if (!v) return []
  if (Array.isArray(v)) return v
  if (typeof v === 'string') {
    try {
      const p = JSON.parse(v)
      return Array.isArray(p) ? p : []
    } catch (e) {
      return []
    }
  }
  return []
}

const evidenceList = computed(() => {
  const d = detail.value
  if (!d) return []
  if (Array.isArray(d.evidence)) return d.evidence
  return parseMaybeJson(d.evidence_json)
})
const actionList = computed(() => {
  const d = detail.value
  if (!d) return []
  if (Array.isArray(d.actions)) return d.actions
  return parseMaybeJson(d.actions_json)
})

async function load() {
  loading.value = true
  try {
    const data = await api.get('/reports', { page: page.value, size: size.value, q: q.value || '' })
    items.value = (data && data.items) || []
    total.value = (data && data.total) || 0
  } catch (e) {
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function onSearch() {
  page.value = 1
  load()
}
function onSizeChange() {
  page.value = 1
  load()
}

async function loadNotifications() {
  notifLoading.value = true
  try {
    const data = await api.get('/reports/notifications', { unread_only: false })
    notifications.value = (data && data.items) || []
    unreadCount.value = (data && data.unread_count) || 0
  } catch (e) {
    notifications.value = []
    unreadCount.value = 0
  } finally {
    notifLoading.value = false
  }
}

async function markRead(n) {
  if (n.read) return
  try {
    const data = await api.post('/reports/notifications/' + n.id + '/read')
    n.read = true
    unreadCount.value =
      data && data.unread_count != null ? data.unread_count : Math.max(0, unreadCount.value - 1)
  } catch (e) {
    /* api 层已提示 */
  }
}

async function markAllRead() {
  markingAll.value = true
  try {
    const list = notifications.value.filter((n) => !n.read)
    for (const n of list) {
      try {
        const data = await api.post('/reports/notifications/' + n.id + '/read')
        n.read = true
        if (data && data.unread_count != null) unreadCount.value = data.unread_count
      } catch (e) {
        /* 单条失败继续 */
      }
    }
    await loadNotifications()
    ElMessage.success('已全部标为已读')
  } finally {
    markingAll.value = false
  }
}

async function openReport(row) {
  dialog.value = true
  detailLoading.value = true
  detail.value = null
  try {
    detail.value = await api.get('/reports/' + row.id)
  } catch (e) {
    detail.value = row
  } finally {
    detailLoading.value = false
  }
}

onMounted(() => {
  load()
  loadNotifications()
})
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
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 4px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}
.notif-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.notif-hint {
  font-size: 12.5px;
  color: #9ca3af;
}
.notif-item {
  display: flex;
  gap: 10px;
  padding: 12px 12px;
  border: 1px solid #eef0f4;
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: background 0.15s;
}
.notif-item:hover {
  background: #fafbfc;
}
.notif-item.unread {
  background: #fffdf7;
  border-color: #fde68a;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-top: 6px;
  flex: none;
  background: transparent;
}
.dot.show {
  background: #ef4444;
}
.notif-main {
  flex: 1;
  min-width: 0;
}
.notif-title {
  font-size: 13.5px;
  color: #374151;
  display: flex;
  align-items: center;
  gap: 8px;
}
.notif-title.bold {
  font-weight: 600;
  color: #111827;
}
.notif-content {
  margin-top: 4px;
  font-size: 13px;
  color: #6b7280;
  line-height: 1.7;
}
.notif-time {
  margin-top: 4px;
  font-size: 12px;
  color: #b0b7c3;
}
.dialog-loading {
  height: 160px;
}
.d-row {
  display: flex;
  gap: 12px;
  padding: 7px 0;
  border-bottom: 1px dashed #f1f3f6;
}
.d-label {
  width: 72px;
  flex: none;
  font-size: 13px;
  color: #9ca3af;
}
.d-value {
  font-size: 13.5px;
  color: #374151;
  line-height: 1.75;
  word-break: break-word;
}
.d-value.strong {
  font-weight: 600;
  color: #111827;
}
.d-value.pre {
  white-space: pre-wrap;
}
.gap {
  margin-left: 10px;
  font-size: 12px;
  color: #9ca3af;
}
.d-section {
  margin: 18px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: #374151;
}
.evi {
  border: 1px solid #eef0f4;
  border-radius: 8px;
  padding: 9px 12px;
  margin-bottom: 8px;
  background: #fafbfc;
}
.evi-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #9ca3af;
}
.evi-ref {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
.evi-rating {
  color: #f59e0b;
}
.evi-dist {
  color: #b0b7c3;
}
.evi-snippet {
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.75;
  color: #4b5563;
}
.act-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  font-size: 13.5px;
  color: #374151;
}
.act-label {
  min-width: 160px;
}
</style>

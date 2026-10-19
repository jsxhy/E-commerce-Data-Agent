<template>
  <div class="page chat-page">
    <div class="page-title">数据分析对话</div>
    <div class="page-desc">
      用自然语言提问，Agent 会路由到结构化查询或语义检索，并在需要时请求人工确认动作。
    </div>

    <div ref="scrollEl" class="chat-box" v-loading="loadingHistory">
      <div v-if="!messages.length && !loadingHistory" class="chat-empty">
        <el-empty description="还没有对话，试着问一个关于商品、订单或评价的问题" />
      </div>

      <div v-for="(m, i) in messages" :key="i" class="msg-row" :class="m.role">
        <!-- 用户消息 -->
        <div v-if="m.role === 'user'" class="bubble user-bubble">{{ m.content }}</div>

        <!-- 助手消息 -->
        <div v-else class="assistant-wrap">
          <!-- 过程面板 -->
          <div v-if="m.steps.length" class="proc">
            <div class="proc-head" @click="m.open = !m.open">
              <span class="proc-title">
                <span class="proc-bullet">●</span>
                已处理 · {{ m.steps.length }} 步 · {{ seconds(m) }} 秒
              </span>
              <span class="proc-toggle">{{ m.open ? '收起' : '展开' }}</span>
            </div>
            <div v-show="m.open" class="proc-body">
              <div v-for="s in m.steps" :key="s.seq" class="proc-row">
                <span class="proc-dot" :style="{ color: stageColor(s.stage) }">●</span>
                <span v-if="s.actor" class="proc-actor">{{ s.actor }}</span>
                <span class="proc-stage">{{ stageName(s.stage) }}</span>
                <span class="proc-detail">{{ s.detail }}</span>
                <span class="proc-ms">{{ s.duration_ms }}ms</span>
              </div>
            </div>
          </div>

          <!-- 回答正文 -->
          <div class="bubble asst-bubble" :class="{ 'bubble-error': m.error }">
            <div v-if="m.content" class="md" v-html="renderMarkdown(m.content)"></div>
            <div v-else class="typing">{{ m.error ? '生成失败' : '思考中…' }}</div>
          </div>

          <!-- 确认卡片 -->
          <el-card
            v-if="m.interrupt"
            shadow="never"
            class="confirm-card"
            :class="{ decided: !!m.decision }"
            :body-style="{ padding: '12px 14px' }"
          >
            <div class="confirm-title">{{ m.interrupt.message || '检测到高风险，请确认是否执行以下动作' }}</div>
            <ul class="confirm-actions">
              <li v-for="(a, ai) in m.interrupt.actions || []" :key="ai">
                <span class="ca-label">{{ actionName(a.type, a.label) }}</span>
                <span v-if="a.summary || a.detail" class="ca-detail">{{ a.summary || a.detail }}</span>
              </li>
            </ul>
            <div v-if="!m.decision" class="confirm-btns">
              <el-button type="primary" size="small" :loading="m.confirming" @click="confirmAction(m, 'approve')">
                确认执行
              </el-button>
              <el-button size="small" :disabled="m.confirming" @click="confirmAction(m, 'reject')">取消</el-button>
            </div>
            <div v-else class="confirm-done">
              {{ m.decision === 'approve' ? '已确认执行' : '已取消' }}
              <span v-if="m.decisionMsg"> · {{ m.decisionMsg }}</span>
            </div>
          </el-card>

          <!-- 反馈 / 复制 -->
          <div class="msg-actions">
            <button class="act" :class="{ active: m.feedback === 'up' }" title="有帮助" @click="feedback(m, 'up')">👍</button>
            <button class="act" :class="{ active: m.feedback === 'down' }" title="没帮助" @click="feedback(m, 'down')">👎</button>
            <button class="act" title="复制回答" @click="copy(m)">复制</button>
          </div>
        </div>
      </div>
    </div>

    <div class="composer">
      <div class="composer-input">
        <el-input
          v-model="input"
          type="textarea"
          :rows="4"
          maxlength="1000"
          resize="none"
          placeholder="输入关于商品、订单或评价的问题..."
          @keydown.enter="onEnter"
        />
        <div class="counter">{{ input.length }}/1000</div>
      </div>
      <div class="composer-foot">
        <span class="hint">Enter 发送，Shift + Enter 换行</span>
        <div class="composer-btns">
          <el-button :disabled="sending" @click="clearInput">清空</el-button>
          <el-button type="primary" :loading="sending" :disabled="sending" @click="send">发送</el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api'

const STAGES = {
  starting: { name: '流程启动', color: '#94a3b8' },
  scheduling: { name: '调度', color: '#6366f1' },
  routing: { name: '意图路由', color: '#2563eb' },
  'agent.analysis': { name: '多角色分析', color: '#8b5cf6' },
  querying: { name: '数据查询', color: '#0ea5e9' },
  deepening: { name: '语义深挖', color: '#06b6d4' },
  preparing_evidence: { name: '证据整合', color: '#14b8a6' },
  'agent.report': { name: '报告决策', color: '#f59e0b' },
  deciding: { name: '风险判断', color: '#f97316' },
  action_decision: { name: '动作决策', color: '#ef4444' },
  action_pending: { name: '等待确认', color: '#f59e0b' },
  executing: { name: '动作执行', color: '#22c55e' },
  finished: { name: '收尾', color: '#16a34a' }
}

const ACTION_NAMES = {
  save_diagnosis_report: '保存诊断报告',
  send_alert_notification: '发送站内预警',
  mark_product_pending_review: '标记商品待人工复核'
}

const messages = ref([])
const input = ref('')
const sending = ref(false)
const loadingHistory = ref(false)
const scrollEl = ref(null)
const sessionId = ref(null)

function stageName(s) {
  return (STAGES[s] && STAGES[s].name) || s || '处理中'
}
function stageColor(s) {
  return (STAGES[s] && STAGES[s].color) || '#94a3b8'
}
function actionName(type, fallback) {
  return ACTION_NAMES[type] || fallback || type
}
function seconds(m) {
  const ms = m.durationMs || m.steps.reduce((a, b) => a + (b.duration_ms || 0), 0)
  return (ms / 1000).toFixed(1)
}

/* ---------- 轻量 markdown ---------- */
function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

const MD_TOKEN = '@@MDCODE' + Math.random().toString(36).slice(2, 8).toUpperCase() + '_'

function renderMarkdown(text) {
  if (!text) return ''
  const blocks = []
  const tokenRe = new RegExp(MD_TOKEN + '(\\d+)@@')
  let src = escapeHtml(text).replace(/```[a-zA-Z0-9_+-]*\n?([\s\S]*?)```/g, (m, code) => {
    blocks.push('<pre class="md-pre"><code>' + code.replace(/\n$/, '') + '</code></pre>')
    return MD_TOKEN + (blocks.length - 1) + '@@'
  })

  const inline = (s) =>
    s
      .replace(/`([^`]+)`/g, '<code class="md-inline">$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')

  const out = []
  let list = null
  const closeList = () => {
    if (list) {
      out.push(list === 'ul' ? '</ul>' : '</ol>')
      list = null
    }
  }

  for (const raw of src.split('\n')) {
    const line = raw.trim()
    if (!line) {
      closeList()
      continue
    }
    if (tokenRe.test(line)) {
      closeList()
      out.push(line)
      continue
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/)
    if (h) {
      closeList()
      const tag = h[1].length <= 2 ? 'h3' : 'h4'
      out.push('<' + tag + ' class="md-h">' + inline(h[2]) + '</' + tag + '>')
      continue
    }
    const ul = line.match(/^[-*+]\s+(.*)$/)
    if (ul) {
      if (list !== 'ul') {
        closeList()
        out.push('<ul class="md-list">')
        list = 'ul'
      }
      out.push('<li>' + inline(ul[1]) + '</li>')
      continue
    }
    const ol = line.match(/^(\d+)[.)]\s+(.*)$/)
    if (ol) {
      if (list !== 'ol') {
        closeList()
        out.push('<ol class="md-list">')
        list = 'ol'
      }
      out.push('<li>' + inline(ol[2]) + '</li>')
      continue
    }
    closeList()
    out.push('<p class="md-p">' + inline(line) + '</p>')
  }
  closeList()
  return out.join('\n').replace(new RegExp(MD_TOKEN + '(\\d+)@@', 'g'), (m, i) => blocks[Number(i)] || '')
}

/* ---------- 滚动 ---------- */
function scrollToBottom() {
  nextTick(() => {
    const el = scrollEl.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function pushMessage(obj) {
  messages.value.push(obj)
  return messages.value[messages.value.length - 1]
}

function newAssistant(extra) {
  return Object.assign(
    {
      role: 'assistant',
      content: '',
      runId: null,
      route: '',
      status: '',
      durationMs: 0,
      steps: [],
      open: true,
      feedback: '',
      error: false,
      interrupt: null,
      decision: '',
      decisionMsg: '',
      confirming: false,
      createdAt: ''
    },
    extra || {}
  )
}

/* ---------- 历史 ---------- */
async function loadHistory() {
  loadingHistory.value = true
  try {
    const data = await api.get('/chat/history', { limit: 20 })
    const items = (data && data.items) || []
    // 契约未约定顺序：按 created_at 升序回放（都能解析时），否则按返回顺序
    const ordered = items.every((it) => it && it.created_at)
      ? items.slice().sort((a, b) => String(a.created_at).localeCompare(String(b.created_at)))
      : items.slice()
    for (const it of ordered) {
      messages.value.push({ role: 'user', content: it.question })
      messages.value.push(
        newAssistant({
          content: it.answer || '',
          runId: it.run_id,
          route: it.route,
          createdAt: it.created_at
        })
      )
    }
    scrollToBottom()
  } catch (e) {
    /* api 层已提示 */
  } finally {
    loadingHistory.value = false
  }
}

/* ---------- 发送 / SSE ---------- */
function onEnter(e) {
  if (e && e.shiftKey) return
  if (e && e.isComposing) return
  e.preventDefault()
  send()
}

function clearInput() {
  input.value = ''
}

async function send() {
  const question = input.value.trim()
  if (!question || sending.value) return
  input.value = ''
  sending.value = true
  messages.value.push({ role: 'user', content: question })
  const msg = pushMessage(newAssistant())
  scrollToBottom()

  try {
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + (localStorage.getItem('token') || '')
      },
      body: JSON.stringify({ question, session_id: sessionId.value })
    })
    if (!res.ok || !res.body) {
      let text = ''
      try {
        text = await res.text()
      } catch (e2) {
        text = ''
      }
      let m2 = ''
      try {
        m2 = JSON.parse(text).message || ''
      } catch (e3) {
        m2 = text || ''
      }
      throw new Error(m2 || '请求失败 HTTP ' + res.status)
    }

    const reader = res.body.getReader()
    const dec = new TextDecoder('utf-8')
    let buf = ''
    let finished = false

    const handleFrame = (frame) => {
      const lines = frame.split('\n')
      let evt = 'message'
      const dataLines = []
      for (const ln of lines) {
        if (ln.startsWith('event:')) evt = ln.slice(6).trim()
        else if (ln.startsWith('data:')) dataLines.push(ln.slice(5).trim())
      }
      if (!dataLines.length) return
      let data = dataLines.join('\n')
      try {
        data = JSON.parse(data)
      } catch (e4) {
        /* 保留原始字符串 */
      }
      onEvent(evt, data, msg, () => (finished = true))
    }

    while (true) {
      const r = await reader.read()
      if (r.done) break
      buf += dec.decode(r.value, { stream: true }).replace(/\r\n/g, '\n')
      let idx
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        if (frame.trim()) handleFrame(frame)
      }
      scrollToBottom()
    }
    if (buf.trim()) handleFrame(buf)

    if (!finished && !msg.error && !msg.content) {
      msg.error = true
      ElMessage.error('连接已断开，未收到完整回答')
    }
  } catch (e) {
    msg.error = true
    ElMessage.error(e.message || '请求失败')
  } finally {
    sending.value = false
    scrollToBottom()
  }
}

function onEvent(evt, data, msg, markFinished) {
  if (evt === 'meta') {
    msg.runId = data.run_id
    msg.route = data.route || msg.route
    if (data.session_id) sessionId.value = data.session_id
    msg.createdAt = data.created_at || ''
  } else if (evt === 'step') {
    msg.steps.push(data)
  } else if (evt === 'token') {
    msg.content += data.delta || ''
  } else if (evt === 'interrupt') {
    msg.interrupt = data
  } else if (evt === 'done') {
    markFinished()
    msg.status = data.status || ''
    msg.route = data.route || msg.route
    if (data.duration_ms) msg.durationMs = data.duration_ms
    if (!msg.content && data.answer) msg.content = data.answer
    if (data.run_id) msg.runId = data.run_id
    if (data.awaiting_confirm && !msg.interrupt) msg.interrupt = { actions: [], message: '' }
    if (data.status === 'failed') msg.error = true
  } else if (evt === 'error') {
    markFinished()
    msg.error = true
    ElMessage.error((data && data.message) || '分析失败')
    if (!msg.content) msg.content = '本次分析失败：' + ((data && data.message) || '未知错误')
  }
}

/* ---------- 确认 / 反馈 / 复制 ---------- */
async function confirmAction(m, decision) {
  if (m.confirming) return
  m.confirming = true
  try {
    const data = await api.post('/chat/confirm', { run_id: m.runId, decision })
    m.decision = decision
    m.decisionMsg = (data && data.message) || ''
    ElMessage.success((data && data.message) || (decision === 'approve' ? '已执行' : '已取消'))
  } catch (e) {
    /* api 层已提示 */
  } finally {
    m.confirming = false
  }
}

async function feedback(m, value) {
  if (!m.runId) return
  const next = m.feedback === value ? null : value
  try {
    const data = await api.post('/runs/' + m.runId + '/feedback', { value: next })
    m.feedback = (data && data.feedback) || next || ''
  } catch (e) {
    /* api 层已提示 */
  }
}

async function copy(m) {
  try {
    await navigator.clipboard.writeText(m.content || '')
    ElMessage.success('已复制')
  } catch (e) {
    ElMessage.error('复制失败，请手动选择文本')
  }
}

onMounted(loadHistory)
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
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100%;
}
.chat-box {
  flex: 1;
  min-height: 320px;
  overflow-y: auto;
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
  border: 1px solid #eef0f4;
}
.chat-empty {
  padding-top: 60px;
}
.msg-row {
  display: flex;
  margin-bottom: 18px;
}
.msg-row.user {
  justify-content: flex-end;
}
.assistant-wrap {
  max-width: 82%;
  width: 100%;
}
.bubble {
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 14px;
  line-height: 1.75;
  word-break: break-word;
}
.user-bubble {
  max-width: 72%;
  background: #2563eb;
  color: #fff;
  white-space: pre-wrap;
}
.asst-bubble {
  background: #fff;
  border: 1px solid #e8ebf0;
  color: #1f2937;
}
.bubble-error {
  border-color: #fca5a5;
  background: #fef2f2;
}
.typing {
  color: #9ca3af;
  font-size: 13px;
}
.proc {
  margin-bottom: 8px;
  background: #fafbfc;
  border: 1px solid #eef0f4;
  border-radius: 8px;
  padding: 8px 12px;
}
.proc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  font-size: 12.5px;
  color: #6b7280;
}
.proc-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.proc-bullet {
  color: #16a34a;
  font-size: 10px;
}
.proc-toggle {
  color: #2563eb;
  user-select: none;
}
.proc-body {
  margin-top: 6px;
  border-left: 2px solid #e5e7eb;
  padding-left: 10px;
}
.proc-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.8;
  color: #4b5563;
}
.proc-dot {
  font-size: 9px;
  flex: none;
}
.proc-actor {
  color: #6b7280;
  flex: none;
}
.proc-stage {
  color: #111827;
  flex: none;
}
.proc-detail {
  color: #6b7280;
  flex: 1;
  min-width: 0;
}
.proc-ms {
  color: #9ca3af;
  flex: none;
}
.confirm-card {
  margin-top: 10px;
  border: 1px solid #f59e0b;
  background: #fffbeb;
  border-radius: 10px;
  padding: 0;
  --el-card-border-color: #f59e0b;
  --el-card-padding: 0;
}
.confirm-card.decided {
  border-color: #d1d5db;
  background: #f9fafb;
  color: #9ca3af;
  --el-card-border-color: #d1d5db;
}
.confirm-title {
  font-size: 13.5px;
  font-weight: 600;
  color: #b45309;
  margin-bottom: 8px;
}
.confirm-card.decided .confirm-title {
  color: #6b7280;
}
.confirm-actions {
  margin: 0 0 10px;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.9;
}
.ca-label {
  color: #111827;
}
.confirm-card.decided .ca-label {
  color: #6b7280;
}
.ca-detail {
  margin-left: 8px;
  color: #9ca3af;
}
.confirm-btns {
  display: flex;
  gap: 8px;
}
.confirm-done {
  font-size: 13px;
  color: #6b7280;
}
.msg-actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}
.act {
  border: 1px solid #eef0f4;
  background: #fff;
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 12.5px;
  color: #6b7280;
  cursor: pointer;
  line-height: 1.6;
}
.act:hover {
  border-color: #c7d2fe;
  color: #2563eb;
}
.act.active {
  border-color: #2563eb;
  color: #2563eb;
  background: #eff6ff;
}
.composer {
  margin-top: 14px;
  background: #fff;
  border: 1px solid #eef0f4;
  border-radius: 10px;
  padding: 12px 14px 10px;
}
.composer-input {
  position: relative;
}
.counter {
  position: absolute;
  right: 10px;
  bottom: 6px;
  font-size: 12px;
  color: #b0b7c3;
  pointer-events: none;
}
.composer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}
.hint {
  font-size: 12px;
  color: #9ca3af;
}
.composer-btns {
  display: flex;
  gap: 8px;
}
/* markdown */
.md :deep(.md-h) {
  margin: 12px 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: #111827;
}
.md :deep(.md-p) {
  margin: 4px 0;
}
.md :deep(.md-list) {
  margin: 4px 0;
  padding-left: 22px;
}
.md :deep(.md-list li) {
  margin: 2px 0;
}
.md :deep(.md-inline) {
  background: #f3f4f6;
  border-radius: 4px;
  padding: 1px 5px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  color: #b91c1c;
}
.md :deep(.md-pre) {
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  overflow-x: auto;
  font-size: 12.5px;
  line-height: 1.7;
  margin: 8px 0;
}
.md :deep(strong) {
  font-weight: 600;
  color: #111827;
}
</style>

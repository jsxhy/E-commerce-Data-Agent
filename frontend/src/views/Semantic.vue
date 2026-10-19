<template>
  <div class="semantic">
    <PageHeader
      title="语义检索"
      desc="这里跳过路由判断，直接把一句话送进向量库，看命中了哪些片段、距离是多少。距离越小越相似（余弦距离=1-相似度），0.5 是当前配置里的相关/不相关分界。"
    />

    <section class="app-card semantic__bar">
      <div class="semantic__input-row">
        <el-input
          v-model="query"
          class="semantic__input"
          placeholder="输入一句话，例如：跑鞋脱胶"
          clearable
          @keyup.enter="onSearch"
        />
        <el-select v-model="topK" class="semantic__select">
          <el-option label="Top 5" :value="5" />
          <el-option label="Top 3" :value="3" />
          <el-option label="Top 10" :value="10" />
        </el-select>
        <el-button type="primary" :loading="loading" @click="onSearch">检索</el-button>
      </div>
      <div class="semantic__quick">
        <el-button
          v-for="word in quickWords"
          :key="word"
          plain
          round
          size="small"
          @click="onQuick(word)"
        >
          {{ word }}
        </el-button>
      </div>
    </section>

    <el-alert
      v-if="unavailable"
      class="semantic__alert"
      type="warning"
      :closable="false"
      show-icon
      title="向量检索暂不可用"
      description="未找到本地 embedding 模型，请运行 python backend/scripts/fetch_model.py"
    />

    <section v-if="!unavailable" class="semantic__result-head">
      <span class="semantic__stat">命中 {{ result.hit_count }} 条 · 向量库共 {{ result.collection_size }} 条文档</span>
      <el-tag v-if="result.embedding_mode" size="small" effect="plain" type="info">
        {{ result.embedding_mode }}
      </el-tag>
    </section>

    <template v-if="!unavailable">
      <div v-if="loading" class="semantic__list">
        <div v-for="n in 3" :key="n" class="app-card">
          <el-skeleton :rows="3" animated />
        </div>
      </div>
      <div v-else-if="result.items.length === 0" class="app-card app-empty">
        {{ searched ? '没有命中的片段，换个说法再试试。' : '输入一句话开始检索。' }}
      </div>
      <div v-else class="semantic__list">
        <article
          v-for="item in result.items"
          :key="`${item.source}-${item.ref_id}-${item.rank}`"
          class="app-card semantic__item"
        >
          <div class="semantic__item-head">
            <span class="semantic__rank">#{{ item.rank }}</span>
            <el-tag size="small" :type="item.source === 'review' ? 'primary' : 'success'" effect="light">
              {{ sourceLabel(item.source) }}
            </el-tag>
            <el-rate
              v-if="item.source !== 'product' && item.rating != null"
              :model-value="Number(item.rating || 0)"
              disabled
              size="small"
              class="semantic__rate"
            />
            <span class="semantic__name">{{ item.product_name }}</span>
          </div>
          <p class="semantic__snippet">{{ item.snippet }}</p>
          <div class="semantic__item-foot">
            <span class="semantic__from">{{ sourceText(item) }}</span>
          </div>
          <div class="semantic__distance" :class="{ 'is-irrelevant': !item.relevant }">
            <div class="semantic__distance-value">距离 {{ formatDistance(item.distance) }}</div>
            <div v-if="!item.relevant" class="semantic__distance-hint">不相关</div>
          </div>
        </article>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../api/index.js'
import PageHeader from '../components/PageHeader.vue'

const quickWords = ['跑鞋脱胶', '耳机有杂音', '洗了缩水', '包装破损']

const query = ref('')
const topK = ref(5)
const loading = ref(false)
const searched = ref(false)
const result = ref({
  query: '',
  top_k: 5,
  hit_count: 0,
  collection_size: 0,
  embedding_mode: '',
  threshold: 0.5,
  items: []
})

/** 后端降级态：embedding 模型或向量库不可用，返回 HTTP 200 + embedding_mode="unavailable"。 */
const unavailable = computed(() => result.value.embedding_mode === 'unavailable')

function sourceLabel(source) {
  return source === 'product' ? '商品' : '评价'
}

function sourceText(item) {
  const threshold = Number(result.value.threshold ?? 0.5)
  const base = item.source === 'product' ? '来源商品描述' : '来源买家评价'
  return `${base} · 距离 ≤ ${threshold}，判定为相关`
}

function formatDistance(value) {
  return Number(value || 0).toFixed(3)
}

async function run(q) {
  const keyword = String(q || '').trim()
  if (!keyword) {
    ElMessage.warning('请输入检索内容')
    return
  }
  loading.value = true
  try {
    const res = await api.get('/semantic/search', { q: keyword, top_k: topK.value })
    result.value = {
      query: (res && res.query) || keyword,
      top_k: (res && res.top_k) || topK.value,
      hit_count: (res && res.hit_count) || 0,
      collection_size: (res && res.collection_size) || 0,
      embedding_mode: (res && res.embedding_mode) || '',
      threshold: res && res.threshold != null ? res.threshold : 0.5,
      items: (res && res.items) || []
    }
    searched.value = true
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function onSearch() {
  run(query.value)
}

function onQuick(word) {
  query.value = word
  run(word)
}

onMounted(() => {
  run('跑鞋脱胶')
})
</script>

<style scoped>
.semantic {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.semantic__bar {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.semantic__alert {
  border-radius: 10px;
}

.semantic__input-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.semantic__input {
  width: 480px;
  max-width: 100%;
}

.semantic__select {
  width: 120px;
  flex: 0 0 120px;
}

.semantic__quick {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.semantic__result-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 2px;
}

.semantic__stat {
  font-size: 12px;
  color: #9ca3af;
}

.semantic__list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.semantic__item {
  position: relative;
  padding-right: 150px;
}

.semantic__item-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.semantic__rank {
  font-size: 13px;
  font-weight: 700;
  color: #1d4ed8;
}

.semantic__rate {
  height: 20px;
}

.semantic__name {
  font-size: 14px;
  font-weight: 700;
  color: #111827;
}

.semantic__snippet {
  margin: 10px 0 0;
  font-size: 14px;
  line-height: 22px;
  color: #374151;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.semantic__item-foot {
  margin-top: 8px;
}

.semantic__from {
  font-size: 12px;
  color: #9ca3af;
}

.semantic__distance {
  position: absolute;
  top: 50%;
  right: 20px;
  transform: translateY(-50%);
  text-align: right;
}

.semantic__distance-value {
  font-size: 20px;
  font-weight: 700;
  color: #16a34a;
  line-height: 26px;
  white-space: nowrap;
}

.semantic__distance-hint {
  margin-top: 2px;
  font-size: 12px;
  color: #9ca3af;
}

.semantic__distance.is-irrelevant .semantic__distance-value {
  font-size: 16px;
  font-weight: 600;
  color: #9ca3af;
}

@media (max-width: 900px) {
  .semantic__input-row {
    flex-wrap: wrap;
  }

  .semantic__item {
    padding-right: 20px;
    padding-bottom: 44px;
  }

  .semantic__distance {
    top: auto;
    bottom: 16px;
    right: 20px;
    transform: none;
  }
}
</style>

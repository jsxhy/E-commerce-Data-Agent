<template>
  <div class="page">
    <div class="page-title">评价数据</div>
    <div class="page-desc">买家评价原文与星级分布，含图片评价的图片描述，是 Agent 判定商品健康度的主要证据来源。</div>

    <div class="card">
      <div class="toolbar">
        <el-input
          v-model="q"
          placeholder="按商品/内容/买家搜索"
          clearable
          style="width: 320px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
        <el-button type="primary" @click="onSearch">查询</el-button>
      </div>

      <el-table v-loading="loading" :data="items" empty-text="暂无评价数据">
        <template #empty>
          <el-empty description="暂无数据" :image-size="70" />
        </template>
        <el-table-column prop="product_name" label="商品名" min-width="190" show-overflow-tooltip />
        <el-table-column label="星级" width="150">
          <template #default="{ row }">
            <el-rate :model-value="row.rating" disabled size="small" />
          </template>
        </el-table-column>
        <el-table-column label="内容" min-width="300">
          <template #default="{ row }">
            <el-tooltip placement="top-start" :content="row.content || ''" :show-after="300">
              <span class="ellipsis">{{ row.content }}</span>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column prop="buyer" label="买家" width="110" />
        <el-table-column label="有图" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.has_image" size="small" effect="light" type="primary">有图</el-tag>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="评价时间" width="170" />
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
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const q = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const data = await api.get('/reviews', { page: page.value, size: size.value, q: q.value || '' })
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
.toolbar {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}
.ellipsis {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: #4b5563;
}
.muted {
  color: #b0b7c3;
}
</style>

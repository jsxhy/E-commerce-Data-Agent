<template>
  <div class="page">
    <div class="page-title">商品数据</div>
    <div class="page-desc">商品基础档案与健康度：价格、库存、上架状态，以及 Agent 标记的待人工复核商品。</div>

    <div class="card">
      <div class="toolbar">
        <el-input
          v-model="q"
          placeholder="按商品名/分类搜索"
          clearable
          style="width: 320px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
        <el-button type="primary" @click="onSearch">查询</el-button>
      </div>

      <el-table v-loading="loading" :data="items" empty-text="暂无商品数据">
        <template #empty>
          <el-empty description="暂无数据" :image-size="70" />
        </template>
        <el-table-column prop="name" label="商品名" min-width="200" show-overflow-tooltip />
        <el-table-column label="分类" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.category" size="small" effect="plain">{{ row.category }}</el-tag>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="价格" width="120" align="right">
          <template #default="{ row }">
            <span class="money">¥{{ fmtMoney(row.price) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="stock" label="库存" width="100" align="right" />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.status === 'on_sale'" type="success" size="small" effect="light">在售</el-tag>
            <el-tag v-else type="info" size="small" effect="light">下架</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="待复核" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.pending_review" type="warning" size="small" effect="light">待复核</el-tag>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
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

function fmtMoney(v) {
  const n = Number(v || 0)
  return n.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

async function load() {
  loading.value = true
  try {
    const data = await api.get('/products', { page: page.value, size: size.value, q: q.value || '' })
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
.money {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
  color: #111827;
}
.muted {
  color: #b0b7c3;
}
</style>

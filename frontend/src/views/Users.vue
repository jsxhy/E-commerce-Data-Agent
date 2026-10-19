<template>
  <div class="page">
    <div class="page-title">用户管理</div>
    <div class="page-desc">系统账号列表（只读）。管理员可查看全部账号的角色与创建时间，账号的增删改由后端统一维护。</div>

    <div class="card">
      <div class="toolbar">
        <el-input
          v-model="q"
          placeholder="按用户名/显示名搜索"
          clearable
          style="width: 320px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
        <el-button type="primary" @click="onSearch">查询</el-button>
      </div>

      <el-table v-loading="loading" :data="shownItems" empty-text="暂无用户数据">
        <template #empty>
          <el-empty description="暂无数据" :image-size="70" />
        </template>
        <el-table-column label="用户名" width="180">
          <template #default="{ row }">
            <span class="mono">{{ row.username }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="display_name" label="显示名" min-width="160" />
        <el-table-column label="角色" width="120">
          <template #default="{ row }">
            <el-tag :type="row.role === 'admin' ? 'danger' : 'info'" size="small" effect="light">
              {{ roleName(row.role) }}
            </el-tag>
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
import { ref, computed, onMounted } from 'vue'
import api from '../api'

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const q = ref('')
const loading = ref(false)

function roleName(r) {
  if (r === 'admin') return '管理员'
  if (r === 'analyst') return '分析师'
  if (r === 'viewer') return '只读'
  return r || '-'
}

async function load() {
  loading.value = true
  try {
    // 契约 §3 的 /users 只声明 page/size，后端也未实现 q；因此搜索在本页结果上做前端过滤
    const data = await api.get('/users', { page: page.value, size: size.value })
    items.value = (data && data.items) || []
    total.value = (data && data.total) || 0
  } catch (e) {
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

const shownItems = computed(() => {
  const kw = q.value.trim().toLowerCase()
  if (!kw) return items.value
  return items.value.filter(
    (u) =>
      String(u.username || '').toLowerCase().includes(kw) ||
      String(u.display_name || '').toLowerCase().includes(kw)
  )
})

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
.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  color: #374151;
}
</style>

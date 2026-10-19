<template>
  <router-view v-if="isPlainLayout" />
  <div v-else class="app-shell">
    <aside class="app-sidebar">
      <div class="app-logo">电商数据分析 Agent</div>
      <el-menu
        class="app-menu"
        :default-active="activePath"
        router
        background-color="#0f172a"
        text-color="#94a3b8"
        active-text-color="#ffffff"
      >
        <el-menu-item v-for="item in menus" :key="item.path" :index="item.path">
          <span>{{ item.title }}</span>
        </el-menu-item>
      </el-menu>
    </aside>

    <div class="app-main">
      <header class="app-header">
        <div class="app-header__title">{{ currentTitle }}</div>
        <div class="app-header__right">
          <el-badge :value="unreadCount" :hidden="unreadCount <= 0" :max="99" class="app-bell">
            <el-button text circle @click="openNotifications">
              <el-icon :size="18"><Bell /></el-icon>
            </el-button>
          </el-badge>
          <el-dropdown trigger="click" @command="onCommand">
            <span class="app-user">
              <el-avatar :size="26" class="app-user__avatar">{{ avatarText }}</el-avatar>
              <span class="app-user__name">{{ auth.displayName || '管理员' }}</span>
              <el-icon class="app-user__caret"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="logout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="app-content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { Bell, ArrowDown } from '@element-plus/icons-vue'
import { useAuthStore } from './stores/auth.js'
import api from './api/index.js'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const menus = [
  { path: '/', title: '系统首页' },
  { path: '/chat', title: '数据分析对话' },
  { path: '/runs', title: '运行历史' },
  { path: '/reports', title: '诊断报告' },
  { path: '/observability', title: '观测看板' },
  { path: '/products', title: '商品数据' },
  { path: '/orders', title: '订单数据' },
  { path: '/reviews', title: '评价数据' },
  { path: '/semantic', title: '语义检索' },
  { path: '/users', title: '用户管理' }
]

const unreadCount = ref(0)

const isPlainLayout = computed(() => route.meta && route.meta.layout === false)
const activePath = computed(() => route.path)
const currentTitle = computed(() => (route.meta && route.meta.title) || '系统首页')
const avatarText = computed(() => (auth.displayName || '管').slice(0, 1))

async function loadUnread() {
  if (!localStorage.getItem('token')) return
  try {
    const data = await api.get('/reports/notifications', { unread_only: true }, { silent: true })
    unreadCount.value = (data && (data.unread_count ?? (data.items || []).length)) || 0
  } catch (e) {
    unreadCount.value = 0
  }
}

function openNotifications() {
  router.push('/reports')
}

async function onCommand(command) {
  if (command !== 'logout') return
  try {
    await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      confirmButtonText: '退出',
      cancelButtonText: '取消',
      type: 'warning'
    })
  } catch (e) {
    return
  }
  auth.logout()
  router.replace('/login')
}

onMounted(loadUnread)
watch(() => route.path, loadUnread)
</script>

<style scoped>
.app-shell {
  display: flex;
  min-height: 100vh;
  background: #f5f7fb;
}

.app-sidebar {
  width: 220px;
  flex: 0 0 220px;
  background: #0f172a;
  display: flex;
  flex-direction: column;
  position: fixed;
  top: 0;
  left: 0;
  bottom: 0;
  z-index: 20;
  overflow-y: auto;
}

.app-logo {
  height: 60px;
  display: flex;
  align-items: center;
  padding: 0 18px;
  color: #ffffff;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.2px;
  border-bottom: 1px solid rgba(148, 163, 184, 0.14);
  flex: 0 0 60px;
  white-space: nowrap;
}

.app-menu {
  border-right: none;
  padding: 10px 8px;
}

.app-menu :deep(.el-menu-item) {
  height: 42px;
  line-height: 42px;
  border-radius: 8px;
  margin-bottom: 4px;
  font-size: 14px;
}

.app-menu :deep(.el-menu-item:hover) {
  background-color: rgba(148, 163, 184, 0.12) !important;
  color: #ffffff;
}

.app-menu :deep(.el-menu-item.is-active) {
  background-color: #1d4ed8 !important;
  color: #ffffff !important;
  font-weight: 600;
}

.app-main {
  flex: 1;
  min-width: 0;
  margin-left: 220px;
  display: flex;
  flex-direction: column;
}

.app-header {
  height: 60px;
  flex: 0 0 60px;
  background: #ffffff;
  border-bottom: 1px solid #e5e7eb;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  position: sticky;
  top: 0;
  z-index: 10;
}

.app-header__title {
  font-size: 18px;
  font-weight: 700;
  color: #111827;
}

.app-header__right {
  display: flex;
  align-items: center;
  gap: 18px;
}

.app-bell :deep(.el-badge__content) {
  background-color: #dc2626;
  border: none;
}

.app-user {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  color: #111827;
  font-size: 14px;
  outline: none;
}

.app-user__avatar {
  background: #1d4ed8;
  color: #ffffff;
  font-size: 13px;
}

.app-user__caret {
  color: #9ca3af;
}

.app-content {
  flex: 1;
  min-width: 0;
  padding: 20px;
  background: #f5f7fb;
}
</style>

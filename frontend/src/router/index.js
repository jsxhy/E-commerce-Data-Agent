import { createRouter, createWebHistory } from 'vue-router'

import Login from '../views/Login.vue'
import Home from '../views/Home.vue'
import Observability from '../views/Observability.vue'
import Semantic from '../views/Semantic.vue'
import Chat from '../views/Chat.vue'
import Runs from '../views/Runs.vue'
import Reports from '../views/Reports.vue'
import Products from '../views/Products.vue'
import Orders from '../views/Orders.vue'
import Reviews from '../views/Reviews.vue'
import Users from '../views/Users.vue'

const routes = [
  { path: '/login', name: 'login', component: Login, meta: { layout: false, title: '登录' } },
  { path: '/', name: 'home', component: Home, meta: { title: '系统首页' } },
  { path: '/chat', name: 'chat', component: Chat, meta: { title: '数据分析对话' } },
  { path: '/runs', name: 'runs', component: Runs, meta: { title: '运行历史' } },
  { path: '/reports', name: 'reports', component: Reports, meta: { title: '诊断报告' } },
  { path: '/observability', name: 'observability', component: Observability, meta: { title: '观测看板' } },
  { path: '/products', name: 'products', component: Products, meta: { title: '商品数据' } },
  { path: '/orders', name: 'orders', component: Orders, meta: { title: '订单数据' } },
  { path: '/reviews', name: 'reviews', component: Reviews, meta: { title: '评价数据' } },
  { path: '/semantic', name: 'semantic', component: Semantic, meta: { title: '语义检索' } },
  { path: '/users', name: 'users', component: Users, meta: { title: '用户管理' } },
  { path: '/:pathMatch(.*)*', redirect: '/' }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (!token && to.path !== '/login') return { path: '/login', query: to.fullPath !== '/' ? { redirect: to.fullPath } : undefined }
  if (token && to.path === '/login') return { path: '/' }
  return true
})

router.afterEach((to) => {
  const base = '电商数据分析 Agent'
  document.title = to.meta && to.meta.title ? `${to.meta.title} · ${base}` : base
})

export default router

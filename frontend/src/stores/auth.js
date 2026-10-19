import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import api from '../api/index.js'

function readJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch (e) {
    return fallback
  }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const user = ref(readJSON('user', null))

  const isLoggedIn = computed(() => !!token.value)
  const displayName = computed(() => (user.value && (user.value.display_name || user.value.username)) || '管理员')
  const role = computed(() => (user.value && user.value.role) || 'admin')

  function setSession(payload) {
    token.value = (payload && payload.token) || ''
    user.value = (payload && payload.user) || null
    if (token.value) localStorage.setItem('token', token.value)
    else localStorage.removeItem('token')
    if (user.value) localStorage.setItem('user', JSON.stringify(user.value))
    else localStorage.removeItem('user')
  }

  async function login(username, password) {
    const data = await api.post('/auth/login', { username, password })
    setSession(data)
    return data
  }

  async function fetchMe() {
    const data = await api.get('/auth/me')
    user.value = data
    localStorage.setItem('user', JSON.stringify(data))
    return data
  }

  function logout() {
    token.value = ''
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('user')
  }

  return { token, user, isLoggedIn, displayName, role, setSession, login, fetchMe, logout }
})

export default useAuthStore

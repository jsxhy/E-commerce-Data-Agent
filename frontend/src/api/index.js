import axios from 'axios'
import { ElMessage } from 'element-plus'

const instance = axios.create({
  baseURL: '/api',
  timeout: 30000
})

instance.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers = config.headers || {}
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

function gotoLogin() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
    window.location.href = '/login'
  }
}

instance.interceptors.response.use(
  (res) => {
    const body = res.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code === 0) return body.data
      if (!(res.config && res.config.silent)) ElMessage.error(body.message || '请求失败')
      return Promise.reject(Object.assign(new Error(body.message || '请求失败'), { code: body.code }))
    }
    return body
  },
  (error) => {
    const status = error.response && error.response.status
    if (status === 401) {
      gotoLogin()
    }
    const body = error.response && error.response.data
    const message =
      (body && body.message) ||
      (error.code === 'ECONNABORTED' ? '请求超时，请稍后重试' : '') ||
      error.message ||
      '网络异常'
    const silent = !!(error.config && error.config.silent)
    if (status !== 401 && !silent) ElMessage.error(message)
    return Promise.reject(Object.assign(error, { message }))
  }
)

const api = {
  get(url, params, config = {}) {
    return instance.get(url, { params, ...config })
  },
  post(url, data, config = {}) {
    return instance.post(url, data, config)
  },
  put(url, data, config = {}) {
    return instance.put(url, data, config)
  },
  del(url, params, config = {}) {
    return instance.delete(url, { params, ...config })
  }
}

export { instance }
export default api

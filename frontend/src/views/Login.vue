<template>
  <div class="login-page">
    <div class="login-card">
      <h1 class="login-title">电商数据分析 Agent</h1>
      <p class="login-subtitle">管理端登录</p>

      <el-form ref="formRef" :model="form" :rules="rules" size="large" @submit.prevent="onSubmit">
        <el-form-item prop="username">
          <el-input
            v-model="form.username"
            placeholder="用户名"
            :prefix-icon="User"
            autocomplete="username"
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            :prefix-icon="Lock"
            show-password
            autocomplete="current-password"
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" class="login-submit" :loading="loading" @click="onSubmit">登录</el-button>
        </el-form-item>
      </el-form>

      <p class="login-hint">默认账号 admin / admin123</p>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import { useAuthStore } from '../stores/auth.js'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const formRef = ref(null)
const loading = ref(false)
const form = reactive({ username: 'admin', password: 'admin123' })

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }]
}

async function onSubmit() {
  if (loading.value) return
  if (formRef.value) {
    const ok = await formRef.value.validate().catch(() => false)
    if (!ok) return
  }
  loading.value = true
  try {
    await auth.login(form.username, form.password)
    ElMessage.success('登录成功')
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    router.replace(redirect)
  } catch (e) {
    /* 错误提示由 axios 响应拦截器统一处理 */
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #0f172a 0%, #1e293b 55%, #1d4ed8 160%);
  padding: 24px;
}

.login-card {
  width: 400px;
  max-width: 100%;
  background: #ffffff;
  border-radius: 12px;
  box-shadow: 0 18px 40px rgba(15, 23, 42, 0.28);
  padding: 32px 32px 24px;
}

.login-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: #111827;
  text-align: center;
  letter-spacing: 0.4px;
}

.login-subtitle {
  margin: 8px 0 26px;
  font-size: 13px;
  color: #6b7280;
  text-align: center;
}

.login-submit {
  width: 100%;
}

.login-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: #9ca3af;
  text-align: center;
}
</style>

import { defineStore } from 'pinia'
import http from '@/api/http'

const AUTH_USER_KEY = 'jademirror-auth-user-v1'
const AUTH_TOKEN_KEY = 'jademirror-auth-token-v1'

function readJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch {
    return fallback
  }
}

function writeJSON(key, value) {
  localStorage.setItem(key, JSON.stringify(value))
}

function readToken() {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

function writeToken(token) {
  localStorage.setItem(AUTH_TOKEN_KEY, token || '')
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    currentUser: readJSON(AUTH_USER_KEY, null),
    token: readToken(),
    sessionChecked: false,
  }),
  getters: {
    isLoggedIn: (state) => !!state.currentUser && !!state.token,
    displayName: (state) => state.currentUser?.nickname || state.currentUser?.username || '未登录',
  },
  actions: {
    applyAuth({ user, token }) {
      this.currentUser = user || null
      this.token = token || ''
      writeJSON(AUTH_USER_KEY, this.currentUser)
      writeToken(this.token)
    },
    clearAuth() {
      this.currentUser = null
      this.token = ''
      writeJSON(AUTH_USER_KEY, null)
      writeToken('')
    },
    async register({ username, password, nickname }) {
      const { data } = await http.post('/auth/register', {
        username,
        password,
        nickname,
      })

      this.applyAuth({ user: data.user, token: data.token })
      this.sessionChecked = true
      return data.user
    },
    async guestLogin() {
      const { data } = await http.post('/auth/guest')

      this.applyAuth({ user: data.user, token: data.token })
      this.sessionChecked = true
      return data.user
    },
    async login({ username, password }) {
      const { data } = await http.post('/auth/login', {
        username,
        password,
      })

      this.applyAuth({ user: data.user, token: data.token })
      this.sessionChecked = true
      return data.user
    },
    async fetchMe() {
      if (!this.token) {
        this.clearAuth()
        return null
      }

      const { data } = await http.get('/auth/me')
      this.applyAuth({ user: data.user, token: this.token })
      return data.user
    },
    async ensureSession() {
      if (this.sessionChecked) {
        return this.isLoggedIn
      }

      if (!this.token) {
        this.sessionChecked = true
        this.clearAuth()
        return false
      }

      try {
        await this.fetchMe()
        this.sessionChecked = true
        return true
      } catch {
        this.clearAuth()
        this.sessionChecked = true
        return false
      }
    },
    async logout() {
      try {
        if (this.token) {
          await http.post('/auth/logout')
        }
      } catch {
        // ignore logout network errors
      }

      // 清空认证信息
      this.clearAuth()
      this.sessionChecked = true
      
      // 清空用户数据
      const { useUserStore } = await import('./userStore')
      const userStore = useUserStore()
      userStore.resetTest()  // 清空测试答案、匹配结果、生成的图片等
      userStore.works = []  // 清空展厅作品
      userStore.persistWorks()  // 持久化空数组
      
      // 清空助手数据
      const { useAssistantStore } = await import('./assistantStore')
      const assistantStore = useAssistantStore()
      assistantStore.messages = []  // 清空对话历史
      assistantStore.memories = []  // 清空记忆
      assistantStore.lastMemoryDigest = ''
      assistantStore.guidedTestActive = false  // 重置测试状态
      assistantStore.guidedQuestionIndex = 0
      assistantStore.galleryTourAuto = false  // 停止展厅导览
      assistantStore.teardown()  // 清理定时器
      
      console.log('✅ 用户登出，所有数据已清空')
    },
  },
})

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import QuestionCard from '@/components/QuestionCard.vue'
import { fetchJadeLibrary } from '@/api/jadeLibrary'
import { deepTestQuestions, quickTestQuestions, DEEP_TEST_MODULES } from '@/data/questions'
import { useUserStore } from '@/stores/userStore'
import { computeUserVector, matchJadeByVector, deriveFlowchartPath } from '@/utils/matching'

const userStore = useUserStore()
const router = useRouter()

const answers = reactive({ ...userStore.testAnswers })
const jades = ref([])
const libraryLoading = ref(false)
const submitting = ref(false)
const errorText = ref('')
const currentIndex = ref(0)
const testMode = ref(userStore.testMode || '')
const modeSelected = computed(() => !!testMode.value)

// 监听userStore.testMode的变化，同步到本地testMode
watch(
  () => userStore.testMode,
  (newMode) => {
    if (newMode && newMode !== testMode.value) {
      console.log(`📢 TestView检测到testMode变化: ${testMode.value} → ${newMode}`)
      testMode.value = newMode
      // 清空之前的答案
      for (const key of Object.keys(answers)) {
        delete answers[key]
      }
      currentIndex.value = 0
      errorText.value = ''
    }
  },
  { immediate: false }
)

const currentQuestions = computed(() => {
  if (testMode.value === 'deep') return deepTestQuestions
  if (testMode.value === 'quick') return quickTestQuestions
  return []
})

const allCompleted = computed(() => {
  return currentQuestions.value.every((q) => !!answers[q.id])
})

const answeredCount = computed(() => {
  return currentQuestions.value.filter((q) => answers[q.id]).length
})

const currentQuestion = computed(() => currentQuestions.value[currentIndex.value])
const isFirstQuestion = computed(() => currentIndex.value === 0)
const isLastQuestion = computed(() => currentIndex.value === currentQuestions.value.length - 1)
const currentAnswered = computed(() => currentQuestion.value && !!answers[currentQuestion.value.id])

const progressPercent = computed(() => {
  if (!currentQuestions.value.length) return 0
  return Math.round(((currentIndex.value + 1) / currentQuestions.value.length) * 100)
})

const currentModule = computed(() => {
  if (testMode.value !== 'deep' || !currentQuestion.value) return null
  return DEEP_TEST_MODULES.find((m) => m.key === currentQuestion.value.module) || null
})

const moduleProgress = computed(() => {
  if (testMode.value !== 'deep') return []
  return DEEP_TEST_MODULES.map((m) => {
    const moduleQuestions = deepTestQuestions.filter((q) => q.module === m.key)
    const answered = moduleQuestions.filter((q) => !!answers[q.id]).length
    return { ...m, answered, total: moduleQuestions.length, done: answered === moduleQuestions.length }
  })
})

async function loadJades() {
  if (jades.value.length) return
  libraryLoading.value = true
  errorText.value = ''
  try {
    jades.value = await fetchJadeLibrary()
  } catch (error) {
    errorText.value = error.message || '玉器库加载失败。'
  } finally {
    libraryLoading.value = false
  }
}

function selectMode(mode) {
  testMode.value = mode
  userStore.setTestMode(mode)
  for (const key of Object.keys(answers)) {
    delete answers[key]
  }
  currentIndex.value = 0
  errorText.value = ''
}

function resetAnswers() {
  for (const question of currentQuestions.value) {
    delete answers[question.id]
  }
  currentIndex.value = 0
  errorText.value = ''
}

function goToQuestion(index) {
  if (index < 0 || index >= currentQuestions.value.length) return
  currentIndex.value = index
  errorText.value = ''
}

function goToPrev() {
  if (isFirstQuestion.value) return
  goToQuestion(currentIndex.value - 1)
}

function goToNext() {
  if (!currentAnswered.value) {
    errorText.value = '请先完成当前题目后再进入下一题。'
    return
  }
  if (isLastQuestion.value) return
  goToQuestion(currentIndex.value + 1)
}

// 同步语音/AI路径记录的答案到本地状态，触发自动跳转
watch(
  () => userStore.testAnswers,
  (newVal) => {
    for (const key of Object.keys(newVal)) {
      if (newVal[key] && !answers[key]) {
        answers[key] = newVal[key]
      }
    }
  },
  { deep: true }
)

watch(
  () => answers[currentQuestion.value?.id],
  (newVal, oldVal) => {
    if (newVal && !oldVal && !isLastQuestion.value) {
      setTimeout(() => goToQuestion(currentIndex.value + 1), 400)
    }
  },
)

async function submitMatch() {
  if (!allCompleted.value) {
    errorText.value = '请先完成全部题目，再提交匹配。'
    return
  }

  if (!jades.value.length) {
    await loadJades()
  }
  if (!jades.value.length) return

  submitting.value = true
  errorText.value = ''

  try {
    const userVector = computeUserVector(currentQuestions.value, answers)
    userStore.setUserVector(userVector)

    const result = matchJadeByVector({ jades: jades.value, userVector })

    const flowPath = deriveFlowchartPath(currentQuestions.value, answers, userVector, result.profile)

    userStore.setAllAnswers({ ...answers })
    userStore.setMatchResult({
      jade: result.jade,
      profile: result.profile,
      reason: result.profile.verdict,
      score: result.score,
      mbtiType: result.mbtiType,
      archetype: result.archetype,
      dimensionScores: result.dimensionScores,
      shadowJade: result.shadowJade,
      shadowProfile: result.shadowProfile,
      flowchartPath: flowPath,
    })
    userStore.clearGeneratedResult()

    router.push('/result')
  } catch (error) {
    errorText.value = error.message || '匹配失败，请稍后重试。'
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  loadJades()

  if (userStore.testMode && currentQuestions.value.length) {
    const firstUnanswered = currentQuestions.value.findIndex((q) => !answers[q.id])
    if (firstUnanswered >= 0) {
      currentIndex.value = firstUnanswered
    } else {
      currentIndex.value = currentQuestions.value.length - 1
    }
  }
})
</script>

<template>
  <section class="test section-grid">
    <template v-if="!modeSelected">
      <article class="mode-select jade-card">
        <h2 class="mode-title">选择测试方式</h2>
        <p class="text-muted mode-desc">两种方式皆基于 MBTI、大五人格与荣格原型，通过高维向量空间映射匹配古玉。</p>
        <div class="mode-cards">
          <button type="button" class="mode-card" @click="selectMode('deep')">
            <div class="mode-header">
              <span class="mode-seal">深</span>
              <div>
                <h3>玉鉴本心 · 拾陆问</h3>
                <p class="text-muted">深度测试版</p>
              </div>
            </div>
            <p class="mode-detail">16 道题，四大模块。不仅测表层行为，更测深层动机与压力状态。适合愿意深度探索内心的你。</p>
            <div class="mode-tags">
              <span class="mode-tag">荣格原型</span>
              <span class="mode-tag">大五人格</span>
              <span class="mode-tag">MBTI</span>
              <span class="mode-tag">压力边界</span>
            </div>
          </button>

          <button type="button" class="mode-card" @click="selectMode('quick')">
            <div class="mode-header">
              <span class="mode-seal">微</span>
              <div>
                <h3>玉鉴微影 · 陆问</h3>
                <p class="text-muted">极简高能版</p>
              </div>
            </div>
            <p class="mode-detail">6 道题，刀刀致命。每一题直接对应一个核心维度，快速锁定你的古玉映射。</p>
            <div class="mode-tags">
              <span class="mode-tag">开放性</span>
              <span class="mode-tag">宜人性</span>
              <span class="mode-tag">原型投射</span>
              <span class="mode-tag">秩序感</span>
            </div>
          </button>
        </div>
      </article>
    </template>

    <template v-else>
      <article class="meta jade-card">
        <div class="meta-top">
          <p class="text-muted">
            {{ testMode === 'deep' ? '拾陆问' : '陆问' }} · 第 {{ currentIndex + 1 }} / {{ currentQuestions.length }} 题，已完成 {{ answeredCount }} 题。
          </p>
          <button type="button" class="jade-button secondary mode-switch-btn" @click="selectMode('')">
            切换方式
          </button>
        </div>

        <div class="progress-wrap" aria-label="测试进度">
          <div class="progress-bar" :style="{ width: `${progressPercent}%` }"></div>
        </div>

        <div v-if="testMode === 'deep'" class="module-progress">
          <div
            v-for="m in moduleProgress"
            :key="m.key"
            class="module-step"
            :class="{ active: currentModule?.key === m.key, done: m.done }"
          >
            <span class="module-label">{{ m.label }}</span>
            <span class="module-count">{{ m.answered }}/{{ m.total }}</span>
          </div>
        </div>

        <div v-else class="step-row">
          <button
            v-for="(question, index) in currentQuestions"
            :key="question.id"
            type="button"
            class="step-dot"
            :class="{ active: currentIndex === index, done: !!answers[question.id] }"
            @click="goToQuestion(index)"
          >
            {{ index + 1 }}
          </button>
        </div>

        <div class="actions-row">
          <button type="button" class="jade-button secondary" :disabled="isFirstQuestion" @click="goToPrev">
            上一题
          </button>
          <button v-if="!isLastQuestion" type="button" class="jade-button secondary" @click="goToNext">
            下一题
          </button>
          <button type="button" class="jade-button secondary" @click="resetAnswers">重置答案</button>
          <button
            v-if="isLastQuestion"
            type="button"
            class="jade-button primary"
            :disabled="submitting || libraryLoading"
            @click="submitMatch"
          >
            {{ submitting ? '匹配中...' : '提交并匹配古玉' }}
          </button>
        </div>
        <p v-if="errorText" class="error-text">{{ errorText }}</p>
      </article>

      <article v-if="libraryLoading" class="jade-card loading-card">
        <span class="loading-dot"></span>
        <p>正在加载玉器库...</p>
      </article>

      <QuestionCard
        v-if="currentQuestion"
        v-model="answers[currentQuestion.id]"
        :question="currentQuestion"
        :show-module="testMode === 'deep'"
      />
    </template>
  </section>
</template>

<style scoped>
.mode-select {
  padding: 1.2rem;
}

.mode-title {
  font-size: 1.3rem;
  margin-bottom: 0.3rem;
}

.mode-desc {
  margin-bottom: 1rem;
  font-size: 0.9rem;
}

.mode-cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.85rem;
}

.mode-card {
  border: 1px solid rgba(63, 99, 86, 0.18);
  border-radius: var(--radius-lg);
  background: linear-gradient(160deg, rgba(248, 252, 249, 0.92), rgba(234, 244, 237, 0.72));
  padding: 1rem;
  text-align: left;
  cursor: pointer;
  transition: transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
}

.mode-card:hover {
  transform: translateY(-2px);
  border-color: rgba(43, 85, 72, 0.4);
  box-shadow: 0 12px 24px rgba(46, 82, 72, 0.14);
}

.mode-header {
  display: flex;
  align-items: center;
  gap: 0.7rem;
  margin-bottom: 0.6rem;
}

.mode-seal {
  width: 2.4rem;
  height: 2.4rem;
  display: grid;
  place-items: center;
  border-radius: 999px;
  background: linear-gradient(135deg, #3d6b5e, #6a9b89);
  color: #f0f7f3;
  font-size: 1.1rem;
  font-weight: 700;
  flex-shrink: 0;
}

.mode-header h3 {
  font-size: 1.05rem;
  margin: 0;
}

.mode-header p {
  margin: 0;
  font-size: 0.8rem;
}

.mode-detail {
  font-size: 0.86rem;
  color: var(--ink-500);
  line-height: 1.55;
  margin-bottom: 0.6rem;
}

.mode-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
}

.mode-tag {
  font-size: 0.72rem;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  background: rgba(67, 112, 101, 0.1);
  color: var(--ink-600);
}

.meta {
  padding: 1rem;
  display: grid;
  gap: 0.75rem;
}

.meta-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.mode-switch-btn {
  font-size: 0.82rem;
  padding: 0.35rem 0.7rem;
}

.loading-card {
  padding: 1rem;
  display: flex;
  align-items: center;
  gap: 0.6rem;
}

.progress-wrap {
  width: 100%;
  height: 8px;
  border-radius: 999px;
  background: rgba(205, 221, 212, 0.72);
  overflow: hidden;
}

.progress-bar {
  height: 100%;
  border-radius: inherit;
  background: linear-gradient(135deg, #437065, #7ca493);
  transition: width 0.3s ease;
}

.module-progress {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.module-step {
  display: flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.3rem 0.6rem;
  border-radius: 999px;
  border: 1px solid rgba(58, 93, 80, 0.18);
  background: rgba(248, 252, 249, 0.85);
  font-size: 0.8rem;
  color: var(--ink-500);
  transition: all 0.2s ease;
}

.module-step.active {
  border-color: transparent;
  background: rgba(46, 87, 73, 0.88);
  color: #eff6f3;
}

.module-step.done {
  background: rgba(206, 226, 214, 0.8);
  color: var(--ink-700);
}

.module-label {
  font-weight: 600;
}

.module-count {
  font-size: 0.72rem;
  opacity: 0.75;
}

.step-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.45rem;
}

.step-dot {
  width: 1.9rem;
  height: 1.9rem;
  border-radius: 999px;
  border: 1px solid rgba(58, 93, 80, 0.22);
  background: rgba(248, 252, 249, 0.85);
  color: var(--ink-500);
  cursor: pointer;
}

.step-dot.done {
  background: rgba(206, 226, 214, 0.8);
  color: var(--ink-700);
}

.step-dot.active {
  border-color: transparent;
  background: rgba(46, 87, 73, 0.88);
  color: #eff6f3;
}

.error-text {
  color: var(--danger);
}

@media (max-width: 780px) {
  .mode-cards {
    grid-template-columns: 1fr;
  }
}
</style>

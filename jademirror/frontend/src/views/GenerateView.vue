<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useApiStore } from '@/stores/apiStore'
import { useAudioStore } from '@/stores/audioStore'
import { useUserStore } from '@/stores/userStore'
import { createFallbackJadeDataURL, urlToDataURL } from '@/utils/image'
import { buildImagePrompt } from '@/utils/prompt'

const router = useRouter()
const userStore = useUserStore()
const apiStore = useApiStore()
const audioStore = useAudioStore()

const jade = computed(() => userStore.matchedJade)

const traitLabels = {
  landscape: '山水',
  color: '色泽',
  symbol: '纹样',
  mood: '气韵',
  texture: '质地',
}

const dynastySuffixes = ['良渚', '红山', '龙山', '仰韶', '河姆渡', '大汶口', '三星堆']

const jadeEraLabel = computed(() => {
  if (!jade.value) return ''
  const d = jade.value.dynasty || ''
  if (dynastySuffixes.includes(d)) return `${d}文化`
  return `${d}代`
})

const jadeIntroduction = computed(() => {
  if (!jade.value) return ''
  const j = jade.value
  const parts = []
  if (j.description) parts.push(j.description)
  if (j.traits) {
    const traitParts = []
    for (const [key, val] of Object.entries(j.traits)) {
      const label = traitLabels[key] || key
      traitParts.push(`${label}：${val}`)
    }
    if (traitParts.length) parts.push(traitParts.join('，'))
  }
  return parts.join('。')
})

const promptText = ref(userStore.lastPrompt || '')
const previewImage = ref(userStore.generatedImageDataUrl || '')
const originalImageUrl = ref(userStore.generatedImageOriginalUrl || '')
const pageError = ref('')
const saveNotice = ref('')
const touchPulse = ref(false)

async function generateJade() {
  if (!jade.value) return

  saveNotice.value = ''
  pageError.value = ''

  const prompt = buildImagePrompt({
    answers: userStore.testAnswers,
    jade: jade.value,
    vector: userStore.userVector,
  })

  promptText.value = prompt

  try {
    const imageUrl = await apiStore.generateImage({ prompt })
    if (!imageUrl) throw new Error('生成接口未返回图片地址。')

    let dataUrl
    try { dataUrl = await urlToDataURL(imageUrl) } catch { dataUrl = imageUrl }

    previewImage.value = dataUrl
    originalImageUrl.value = imageUrl
    userStore.setGeneratedResult({ imageDataUrl: dataUrl, prompt, modelUrl: '', originalUrl: imageUrl })
  } catch (error) {
    pageError.value = error.message || '图像生成失败，已回退到占位图。'
    const fallbackDataUrl = createFallbackJadeDataURL(jade.value.name)
    previewImage.value = fallbackDataUrl
    originalImageUrl.value = ''
    userStore.setGeneratedResult({ imageDataUrl: fallbackDataUrl, prompt, modelUrl: '', originalUrl: '' })
  }
}

async function replayTouchSound() {
  if (!jade.value) return
  try {
    await audioStore.playJadeMelody({ jade: jade.value, mode: 'touch' })
    touchPulse.value = true
    window.setTimeout(() => { touchPulse.value = false }, 280)
  } catch (error) {
    pageError.value = error.message || '当前浏览器无法播放音效。'
  }
}

function saveToGallery() {
  const work = userStore.saveCurrentWork()
  saveNotice.value = work ? '已保存到个人藏室。' : '请先生成专属玉图像。'
}

async function primeAudioOnce() {
  try { await audioStore.primeContext() } catch { pageError.value = '音频环境初始化失败。' }
}

onMounted(() => {
  window.addEventListener('pointerdown', primeAudioOnce, { once: true })
})
onBeforeUnmount(() => {
  window.removeEventListener('pointerdown', primeAudioOnce)
})
</script>

<template>
  <section class="generate section-grid">
    <article v-if="jade" class="info-card jade-card">
      <div class="info-row">
        <div class="info-text">
          <h3>{{ jadeEraLabel }} · {{ jade.name }}</h3>
          <p class="text-muted">{{ jade.description }}</p>
        </div>
        <div class="actions-row">
          <button type="button" class="jade-button primary" :disabled="apiStore.imageLoading" @click="generateJade">
            {{ apiStore.imageLoading ? '生成中...' : '生成专属玉' }}
          </button>
          <button type="button" class="jade-button secondary" @click="saveToGallery">保存至藏室</button>
        </div>
      </div>
      <p v-if="saveNotice" class="success">{{ saveNotice }}</p>
      <p v-if="pageError" class="error-text">{{ pageError }}</p>
      <p v-if="apiStore.lastError" class="error-text">{{ apiStore.lastError }}</p>
    </article>

    <article class="viewer-card jade-card">
      <div class="image-box" :class="{ active: touchPulse }">
        <img
          v-if="previewImage"
          :src="previewImage"
          alt="生成的专属玉图像"
          class="preview-image"
          @click="replayTouchSound"
        />
        <p v-else class="text-muted placeholder-text">点击上方"生成专属玉"开始创作</p>
      </div>
      <p class="text-muted viewer-tip">
        {{ previewImage ? '点击图像可试听音效' : '点击"生成专属玉"开始创作' }}
      </p>
    </article>

    <article v-if="jade" class="prompt-card jade-card">
      <h4>{{ jadeEraLabel }} · {{ jade.name }}</h4>
      <p class="jade-intro">{{ jadeIntroduction }}</p>
      <p v-if="jade.personality" class="jade-personality">{{ jade.personality }}</p>
    </article>
  </section>
</template>

<style scoped>
.info-card {
  padding: 1rem;
}

.info-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 1rem;
  flex-wrap: wrap;
}

.info-text h3 {
  margin: 0 0 0.3rem;
}

.info-text p {
  margin: 0;
}

.actions-row {
  flex-shrink: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
}

.viewer-card {
  padding: 1rem;
  display: grid;
  gap: 0.5rem;
}

.image-box {
  min-height: 400px;
  display: grid;
  place-items: center;
  border-radius: var(--radius-md);
  border: 1px dashed rgba(54, 89, 76, 0.36);
  background: rgba(242, 248, 244, 0.72);
  text-align: center;
  padding: 0.8rem;
  transition: transform 0.24s ease, box-shadow 0.24s ease, border-color 0.24s ease;
}

.image-box.active {
  transform: scale(1.01);
  border-color: rgba(46, 97, 79, 0.52);
  box-shadow: 0 14px 34px rgba(63, 112, 93, 0.2);
}

.placeholder-text {
  font-size: 0.9rem;
  opacity: 0.6;
}

.preview-image {
  max-width: 100%;
  max-height: 460px;
  border-radius: var(--radius-md);
  object-fit: contain;
  cursor: pointer;
}

.viewer-tip {
  text-align: center;
  font-size: 0.84rem;
  margin: 0;
}

.prompt-card {
  padding: 0.8rem 1rem;
  display: grid;
  gap: 0.4rem;
}

.prompt-card h4 {
  margin: 0;
  font-size: 0.95rem;
}

.jade-intro {
  margin: 0.4rem 0 0;
  font-size: 0.88rem;
  line-height: 1.7;
  color: var(--ink-700);
}

.jade-personality {
  margin: 0.5rem 0 0;
  font-size: 0.84rem;
  line-height: 1.7;
  color: var(--ink-600);
  font-style: italic;
}

.success {
  color: #2c6f57;
  margin: 0.3rem 0 0;
}

.error-text {
  color: var(--danger);
  margin: 0.3rem 0 0;
}

.jade-button.accent {
  background: linear-gradient(135deg, #3a7d68, #2d6b56);
  color: #f0f8f4;
  border-color: #3a7d68;
}

.jade-button.accent:hover:not(:disabled) {
  background: linear-gradient(135deg, #4a8d78, #3d7b66);
}

@media (max-width: 780px) {
  .info-row {
    flex-direction: column;
  }

  .actions-row {
    width: 100%;
  }

  .image-box {
    min-height: 300px;
  }
}
</style>

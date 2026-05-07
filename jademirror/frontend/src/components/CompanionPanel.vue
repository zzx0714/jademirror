<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAssistantStore } from '@/stores/assistantStore'
import { useVoiceStore } from '@/stores/voiceStore'
import { useAuthStore } from '@/stores/authStore'

const route = useRoute()
const router = useRouter()
const assistantStore = useAssistantStore()
const voiceStore = useVoiceStore()
const authStore = useAuthStore()
const draft = ref('')
const holdTalking = ref(false)
const showSettings = ref(false)
const showMemories = ref(false)

// 拖拽相关状态
const isDragging = ref(false)
const dragStartX = ref(0)
const dragStartY = ref(0)
// 初始位置：右下角，留出足够边距
const position = ref({ x: 0, y: 0 })
const companionRef = ref(null)

// 初始化位置
onMounted(() => {
  // 初始化玉灵童子
  assistantStore.welcomeIfNeeded()
  assistantStore.setStage(route.name)
  voiceStore.init()
  assistantStore.touchActivity(router)
  
  // 设置初始位置为右下角
  setTimeout(() => {
    position.value = {
      x: window.innerWidth - 100,
      y: window.innerHeight - 100
    }
    console.log('🐾 玉灵童子初始位置:', position.value)
  }, 100)
  
  if (authStore.isLoggedIn) {
    const welcomeMsg = '我是玉灵童子，你的专属AI管家。你可以直接和我说话，我会主动带你完成照心测试、古玉匹配、对话、生玉与展厅管理。'
    assistantStore.appendMessage('assistant', welcomeMsg)
    assistantStore.speak(welcomeMsg)
  }
  
  window.addEventListener('pointerdown', handleGlobalActivity, true)
  window.addEventListener('keydown', handleGlobalActivity, true)
})

watch(
  () => route.name,
  (name) => {
    assistantStore.setStage(name)
    assistantStore.touchActivity(router)
  },
)

const latestMessages = computed(() => assistantStore.messages.slice(-8))
const memoryPreview = computed(() => assistantStore.filteredMemories.slice(0, 6))
const memoryCounts = computed(() => assistantStore.memoryTypeCounts)
const toneLabel = computed(() => {
  const map = {
    calm: '平和',
    comforting: '安抚',
    cheerful: '轻快',
    energetic: '振奋',
    contemplative: '沉静',
  }
  return map[assistantStore.emotionalTone] || '平和'
})
const personaOptions = [
  { value: 'default', label: '默认声线' },
  { value: 'warm', label: '温润声线' },
  { value: 'bright', label: '清亮声线' },
  { value: 'deep', label: '低沉声线' },
]
const listeningLabel = computed(() => {
  if (assistantStore.busy) {
    return '思考中...'
  }
  if (holdTalking.value || voiceStore.holdListening || voiceStore.listening) {
    return '松开结束'
  }
  return '按住说话'
})

async function sendDraft() {
  const text = draft.value.trim()
  if (!text || assistantStore.busy) {
    return
  }
  draft.value = ''
  await assistantStore.handleUserText(text, router)
}

function beginHoldToTalk() {
  if (assistantStore.busy || holdTalking.value || !voiceStore.recognitionSupported) {
    return
  }
  const started = voiceStore.startHoldListening()
  if (!started) {
    return
  }
  holdTalking.value = true
}

async function endHoldToTalk() {
  if (!holdTalking.value) {
    return
  }
  holdTalking.value = false
  const transcript = await voiceStore.stopHoldListening()
  const text = String(transcript || '').trim()
  if (!text) {
    return
  }
  await assistantStore.handleUserText(text, router)
}

async function nudgeNow() {
  await assistantStore.triggerIdleNudge(router)
}

async function refreshMemories() {
  await assistantStore.loadMemories()
}

async function togglePin(memory) {
  await assistantStore.setMemoryPinned(memory.id, !memory.pinned)
}

async function removeMemoryItem(memory) {
  await assistantStore.removeMemory(memory.id)
}

async function clearAll() {
  await assistantStore.clearAllMemories()
}

async function exportAll() {
  await assistantStore.exportMemories()
}

function handleGlobalActivity() {
  assistantStore.touchActivity(router)
}

// 拖拽功能
function startDrag(event) {
  // 如果点击的是面板内容，不触发拖拽
  if (assistantStore.open && event.target.closest('.panel')) {
    return
  }
  
  isDragging.value = true
  dragStartX.value = event.clientX - position.value.x
  dragStartY.value = event.clientY - position.value.y
  
  document.addEventListener('pointermove', onDrag)
  document.addEventListener('pointerup', stopDrag)
}

function onDrag(event) {
  if (!isDragging.value) return
  
  const newX = event.clientX - dragStartX.value
  const newY = event.clientY - dragStartY.value
  
  // 限制在窗口范围内
  const maxX = window.innerWidth - 80
  const maxY = window.innerHeight - 80
  
  position.value = {
    x: Math.max(0, Math.min(newX, maxX)),
    y: Math.max(0, Math.min(newY, maxY))
  }
}

function stopDrag() {
  isDragging.value = false
  document.removeEventListener('pointermove', onDrag)
  document.removeEventListener('pointerup', stopDrag)
}

function togglePanel() {
  if (!isDragging.value) {
    assistantStore.open = !assistantStore.open
  }
}

onMounted(() => {
  window.addEventListener('pointerdown', handleGlobalActivity, true)
  window.addEventListener('keydown', handleGlobalActivity, true)
})

onBeforeUnmount(() => {
  window.removeEventListener('pointerdown', handleGlobalActivity, true)
  window.removeEventListener('keydown', handleGlobalActivity, true)
  document.removeEventListener('pointermove', onDrag)
  document.removeEventListener('pointerup', stopDrag)
  holdTalking.value = false
  voiceStore.stopListening()
  assistantStore.teardown()
})
</script>

<template>
  <aside 
    v-if="authStore.isLoggedIn"
    ref="companionRef"
    class="companion" 
    :class="{ expanded: assistantStore.open, dragging: isDragging }"
    :style="{ left: position.x + 'px', top: position.y + 'px' }"
  >
    <!-- 小动物形象（收起状态） -->
    <div 
      class="pet-avatar" 
      :class="{ hidden: assistantStore.open }"
      @pointerdown="startDrag"
      @click="togglePanel"
    >
      <div class="pet-body">
        <!-- 腮红 -->
        <div class="pet-blush left-blush"></div>
        <div class="pet-blush right-blush"></div>
        
        <!-- 脸部 -->
        <div class="pet-face">
          <!-- 眼睛 -->
          <div class="pet-eyes">
            <div class="eye left-eye">
              <div class="eye-white"></div>
              <div class="eye-pupil"></div>
              <div class="eye-shine"></div>
            </div>
            <div class="eye right-eye">
              <div class="eye-white"></div>
              <div class="eye-pupil"></div>
              <div class="eye-shine"></div>
            </div>
          </div>
          <!-- 嘴巴 -->
          <div class="pet-mouth">
            <svg width="30" height="20" viewBox="0 0 30 20">
              <path d="M 5 5 Q 15 15, 25 5" stroke="#2d3748" stroke-width="2.5" fill="none" stroke-linecap="round"/>
            </svg>
          </div>
        </div>
        
        <!-- 耳朵 -->
        <div class="pet-ears">
          <div class="ear left-ear">
            <div class="ear-inner"></div>
          </div>
          <div class="ear right-ear">
            <div class="ear-inner"></div>
          </div>
        </div>
        
        <!-- 装饰：小玉佩 -->
        <div class="pet-jade-pendant">
          <div class="jade-stone"></div>
        </div>
      </div>
      
      <!-- 状态徽章 -->
      <div class="pet-badge" v-if="assistantStore.busy">
        <span class="thinking-dot"></span>
      </div>
    </div>

    <!-- 展开的面板 -->
    <div v-if="assistantStore.open" class="panel">
      <header class="head">
        <p class="title">玉灵童子</p>
        <button type="button" class="close-btn" @click="assistantStore.open = false">✕</button>
      </header>

      <div class="messages">
        <p v-for="item in latestMessages" :key="item.id" :class="['line', item.role]">
          {{ item.role === 'assistant' ? '童子：' : '你：' }}{{ item.content }}
        </p>
      </div>

      <div class="actions">
        <button
          type="button"
          class="jade-button secondary"
          :class="{ hold: holdTalking || voiceStore.holdListening }"
          :disabled="assistantStore.busy || !voiceStore.recognitionSupported"
          @pointerdown.prevent="beginHoldToTalk"
          @pointerup.prevent="endHoldToTalk"
          @pointerleave.prevent="endHoldToTalk"
          @pointercancel.prevent="endHoldToTalk"
        >
          {{ listeningLabel }}
        </button>
        <button type="button" class="jade-button secondary" :disabled="assistantStore.busy" @click="nudgeNow">
          主动关怀一下
        </button>
      </div>

      <div class="composer">
        <textarea
          v-model="draft"
          rows="2"
          placeholder="直接说：带我开始测试 / 去展厅 / 继续聊天..."
          @keydown.enter.exact.prevent="sendDraft"
        ></textarea>
        <button type="button" class="jade-button primary" :disabled="assistantStore.busy" @click="sendDraft">
          发送
        </button>
      </div>

      <!-- 设置区域（可折叠） -->
      <div class="collapsible-section">
        <button type="button" class="section-toggle" @click="showSettings = !showSettings">
          <span class="toggle-icon">{{ showSettings ? '▼' : '▶' }}</span>
          设置
        </button>
        <div v-if="showSettings" class="section-content">
          <label class="switch">
            <input v-model="assistantStore.autoGuide" type="checkbox" />
            自动跳转到下一步
          </label>
          <label class="switch">
            <input :checked="assistantStore.privacyMode" type="checkbox" @change="assistantStore.setPrivacyMode($event.target.checked)" />
            隐私模式（不保存记忆）
          </label>
          <label class="switch">
            <input v-model="assistantStore.autoSpeak" type="checkbox" />
            自动语音播报
          </label>
          <label class="switch">
            <input v-model="assistantStore.idleEnabled" type="checkbox" @change="assistantStore.touchActivity(router)" />
            空闲时主动闲聊
          </label>
          <div class="persona-row">
            <span class="persona-label">声线角色</span>
            <select class="persona-select" :value="assistantStore.voicePersona" @change="assistantStore.setVoicePersona($event.target.value)">
              <option v-for="item in personaOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
            </select>
          </div>
        </div>
      </div>

      <!-- 长期记忆区域（可折叠） -->
      <div class="collapsible-section">
        <button type="button" class="section-toggle" @click="showMemories = !showMemories">
          <span class="toggle-icon">{{ showMemories ? '▼' : '▶' }}</span>
          长期记忆 ({{ memoryCounts.all }})
        </button>
        <div v-if="showMemories" class="section-content">
          <div class="memory-head-actions">
            <button type="button" class="tiny-btn" :disabled="assistantStore.memoryLoading" @click="refreshMemories">刷新</button>
            <button type="button" class="tiny-btn" :disabled="assistantStore.memoryLoading" @click="exportAll">导出</button>
            <button type="button" class="tiny-btn warn" :disabled="assistantStore.memoryLoading" @click="clearAll">清空</button>
          </div>
          <div class="memory-filters">
            <button type="button" class="tiny-btn" :class="{ active: assistantStore.memoryFilter === 'all' }" @click="assistantStore.setMemoryFilter('all')">
              全部({{ memoryCounts.all }})
            </button>
            <button type="button" class="tiny-btn" :class="{ active: assistantStore.memoryFilter === 'preference' }" @click="assistantStore.setMemoryFilter('preference')">
              偏好({{ memoryCounts.preference }})
            </button>
            <button type="button" class="tiny-btn" :class="{ active: assistantStore.memoryFilter === 'emotion' }" @click="assistantStore.setMemoryFilter('emotion')">
              情绪({{ memoryCounts.emotion }})
            </button>
          </div>
          <div class="memory-list">
            <p v-if="!memoryPreview.length" class="memory-empty text-muted">暂无记忆片段</p>
            <div v-for="memory in memoryPreview" :key="memory.id" class="memory-row">
              <p class="memory-text">{{ memory.content }}</p>
              <div class="memory-actions">
                <button type="button" class="tiny-btn" @click="togglePin(memory)">
                  {{ memory.pinned ? '取消置顶' : '置顶' }}
                </button>
                <button type="button" class="tiny-btn warn" @click="removeMemoryItem(memory)">删除</button>
              </div>
            </div>
          </div>
          <textarea
            v-if="assistantStore.memoryExportText"
            class="export-box"
            readonly
            rows="5"
            :value="assistantStore.memoryExportText"
          ></textarea>
        </div>
      </div>

      <p v-if="assistantStore.lastMemoryDigest" class="digest">记忆摘要：{{ assistantStore.lastMemoryDigest }}</p>
      <p v-if="assistantStore.lastError" class="error-text">{{ assistantStore.lastError }}</p>
    </div>
  </aside>
</template>

<style scoped>
.companion {
  position: fixed;
  z-index: 999;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.companion.dragging {
  transition: none;
  cursor: grabbing;
}

.companion.expanded {
  width: min(420px, calc(100vw - 2rem));
  /* 展开时自动调整位置，确保不超出屏幕 */
  right: 1rem;
  bottom: 1rem;
  left: auto !important;
  top: auto !important;
}

/* 小动物形象 */
.pet-avatar {
  width: 90px;
  height: 90px;
  cursor: grab;
  position: relative;
  transition: transform 0.3s ease, opacity 0.3s ease;
  filter: drop-shadow(0 8px 16px rgba(45, 89, 75, 0.3));
}

.pet-avatar:active {
  cursor: grabbing;
}

.pet-avatar.hidden {
  opacity: 0;
  pointer-events: none;
  transform: scale(0.5);
}

.pet-avatar:hover .pet-body {
  transform: scale(1.08) rotate(-3deg);
}

.pet-body {
  width: 90px;
  height: 90px;
  background: linear-gradient(145deg, #8bc9a8 0%, #5fa882 50%, #4a8c6f 100%);
  border-radius: 50%;
  position: relative;
  box-shadow: 
    0 10px 30px rgba(45, 89, 75, 0.4),
    inset 0 -6px 12px rgba(0, 0, 0, 0.15),
    inset 0 6px 12px rgba(255, 255, 255, 0.4),
    inset -3px 0 8px rgba(0, 0, 0, 0.1),
    inset 3px 0 8px rgba(255, 255, 255, 0.2);
  transition: transform 0.3s ease;
  animation: float 3s ease-in-out infinite;
  overflow: visible;
}

@keyframes float {
  0%, 100% {
    transform: translateY(0px) rotate(0deg);
  }
  25% {
    transform: translateY(-6px) rotate(1deg);
  }
  50% {
    transform: translateY(-10px) rotate(0deg);
  }
  75% {
    transform: translateY(-6px) rotate(-1deg);
  }
}

/* 腮红 */
.pet-blush {
  position: absolute;
  width: 18px;
  height: 14px;
  background: radial-gradient(circle, rgba(255, 182, 193, 0.6) 0%, transparent 70%);
  border-radius: 50%;
  top: 48%;
  animation: blush 3s ease-in-out infinite;
}

.left-blush {
  left: 8px;
}

.right-blush {
  right: 8px;
}

@keyframes blush {
  0%, 100% {
    opacity: 0.6;
  }
  50% {
    opacity: 0.9;
  }
}

/* 脸部 */
.pet-face {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
}

/* 眼睛 */
.pet-eyes {
  display: flex;
  gap: 22px;
  justify-content: center;
  margin-bottom: 8px;
}

.eye {
  width: 16px;
  height: 18px;
  position: relative;
  animation: blink 4s infinite;
}

.eye-white {
  width: 16px;
  height: 18px;
  background: white;
  border-radius: 50% 50% 50% 50% / 60% 60% 40% 40%;
  position: absolute;
  box-shadow: inset 0 -2px 4px rgba(0, 0, 0, 0.1);
}

.eye-pupil {
  width: 8px;
  height: 10px;
  background: #2d3748;
  border-radius: 50%;
  position: absolute;
  top: 6px;
  left: 4px;
  animation: eyeMove 5s ease-in-out infinite;
}

.eye-shine {
  width: 4px;
  height: 4px;
  background: white;
  border-radius: 50%;
  position: absolute;
  top: 7px;
  left: 6px;
  opacity: 0.9;
}

@keyframes blink {
  0%, 48%, 52%, 100% {
    transform: scaleY(1);
  }
  50% {
    transform: scaleY(0.1);
  }
}

@keyframes eyeMove {
  0%, 100% {
    transform: translateX(0);
  }
  25% {
    transform: translateX(-1px);
  }
  75% {
    transform: translateX(1px);
  }
}

/* 嘴巴 */
.pet-mouth {
  margin-top: 2px;
  display: flex;
  justify-content: center;
}

.pet-mouth svg {
  filter: drop-shadow(0 1px 2px rgba(0, 0, 0, 0.1));
}

/* 耳朵 */
.pet-ears {
  position: absolute;
  top: -8px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: 52px;
  z-index: -1;
}

.ear {
  width: 28px;
  height: 32px;
  background: linear-gradient(145deg, #7ab298 0%, #5a9d7a 100%);
  border-radius: 50% 50% 20% 20%;
  position: relative;
  box-shadow: 
    0 4px 8px rgba(0, 0, 0, 0.2),
    inset 0 2px 4px rgba(255, 255, 255, 0.3);
  animation: earWiggle 2s ease-in-out infinite;
}

.left-ear {
  transform: rotate(-25deg);
  animation-delay: 0s;
}

.right-ear {
  transform: rotate(25deg);
  animation-delay: 0.1s;
}

.ear-inner {
  width: 14px;
  height: 18px;
  background: linear-gradient(145deg, #a8d5ba 0%, #8bc9a8 100%);
  border-radius: 50% 50% 20% 20%;
  position: absolute;
  top: 6px;
  left: 50%;
  transform: translateX(-50%);
}

@keyframes earWiggle {
  0%, 100% {
    transform: rotate(-25deg);
  }
  50% {
    transform: rotate(-28deg);
  }
}

.right-ear {
  animation-name: earWiggleRight;
}

@keyframes earWiggleRight {
  0%, 100% {
    transform: rotate(25deg);
  }
  50% {
    transform: rotate(28deg);
  }
}

/* 玉佩装饰 */
.pet-jade-pendant {
  position: absolute;
  bottom: -8px;
  left: 50%;
  transform: translateX(-50%);
  width: 20px;
  height: 24px;
  z-index: 10;
}

.jade-stone {
  width: 20px;
  height: 24px;
  background: linear-gradient(135deg, #b8e6d5 0%, #7ab298 50%, #5a9d7a 100%);
  border-radius: 30% 30% 40% 40%;
  box-shadow: 
    0 4px 8px rgba(45, 89, 75, 0.4),
    inset 0 2px 4px rgba(255, 255, 255, 0.5),
    inset 0 -2px 4px rgba(0, 0, 0, 0.2);
  position: relative;
  animation: pendantSwing 2s ease-in-out infinite;
}

.jade-stone::before {
  content: '';
  position: absolute;
  top: 3px;
  left: 3px;
  width: 6px;
  height: 6px;
  background: rgba(255, 255, 255, 0.6);
  border-radius: 50%;
  filter: blur(1px);
}

.jade-stone::after {
  content: '';
  position: absolute;
  top: -4px;
  left: 50%;
  transform: translateX(-50%);
  width: 2px;
  height: 4px;
  background: #5a9d7a;
  border-radius: 2px;
}

@keyframes pendantSwing {
  0%, 100% {
    transform: rotate(-3deg);
  }
  50% {
    transform: rotate(3deg);
  }
}

/* 状态徽章 */
.pet-badge {
  position: absolute;
  top: -5px;
  right: -5px;
  width: 24px;
  height: 24px;
  background: linear-gradient(135deg, #f59e0b, #ef4444);
  border-radius: 50%;
  border: 3px solid white;
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
}

.thinking-dot {
  width: 8px;
  height: 8px;
  background: white;
  border-radius: 50%;
  animation: pulse 1s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.5);
    opacity: 0.7;
  }
}

/* 展开的面板 */
.panel {
  border-radius: 16px;
  border: 1px solid rgba(57, 96, 82, 0.24);
  background: rgba(252, 254, 253, 0.98);
  backdrop-filter: blur(12px);
  box-shadow: 0 20px 40px rgba(34, 69, 56, 0.2);
  padding: 1rem;
  display: grid;
  gap: 0.6rem;
  animation: slideIn 0.3s ease-out;
}

@keyframes slideIn {
  from {
    opacity: 0;
    transform: translateY(20px) scale(0.95);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid rgba(56, 90, 77, 0.15);
}

.title {
  margin: 0;
  font-weight: 700;
  font-size: 1.1rem;
  color: var(--ink-700);
}

.close-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 1px solid rgba(56, 90, 77, 0.2);
  background: rgba(247, 252, 248, 0.8);
  color: var(--ink-600);
  font-size: 18px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.close-btn:hover {
  background: rgba(239, 247, 242, 1);
  border-color: rgba(56, 90, 77, 0.4);
  transform: rotate(90deg);
}

.messages {
  max-height: 180px;
  overflow: auto;
  border-radius: 10px;
  border: 1px solid rgba(58, 91, 79, 0.16);
  background: rgba(239, 247, 242, 0.65);
  padding: 0.5rem;
  display: grid;
  gap: 0.4rem;
}

.line {
  margin: 0;
  font-size: 0.86rem;
  line-height: 1.5;
}

.line.user {
  color: var(--ink-700);
}

.line.assistant {
  color: #285946;
}

.actions {
  display: flex;
  gap: 0.45rem;
}

.actions .hold {
  background: rgba(45, 89, 75, 0.9);
  color: #eef6f2;
  border-color: transparent;
}

.composer {
  display: grid;
  gap: 0.45rem;
}

.composer textarea {
  width: 100%;
  resize: vertical;
  border: 1px solid rgba(56, 92, 79, 0.22);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.93);
  padding: 0.55rem 0.65rem;
}

.switch {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.82rem;
  color: var(--ink-600);
}

.switch input {
  accent-color: #2f6757;
}

.collapsible-section {
  border: 1px solid rgba(56, 90, 77, 0.16);
  border-radius: 10px;
  background: rgba(247, 252, 249, 0.5);
  overflow: hidden;
}

.section-toggle {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.5rem 0.65rem;
  background: transparent;
  border: none;
  cursor: pointer;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--ink-700);
  text-align: left;
  transition: background 0.2s;
}

.section-toggle:hover {
  background: rgba(239, 247, 242, 0.6);
}

.toggle-icon {
  font-size: 0.7rem;
  color: var(--ink-500);
  transition: transform 0.2s;
}

.section-content {
  padding: 0.5rem 0.65rem;
  border-top: 1px solid rgba(56, 90, 77, 0.12);
  display: grid;
  gap: 0.5rem;
}

.persona-row {
  display: flex;
  align-items: center;
  gap: 0.45rem;
}

.persona-label {
  font-size: 0.82rem;
  color: var(--ink-600);
}

.persona-select {
  border: 1px solid rgba(56, 90, 77, 0.24);
  border-radius: 8px;
  padding: 0.22rem 0.42rem;
  background: rgba(255, 255, 255, 0.94);
  color: var(--ink-700);
}

.error-text {
  margin: 0;
  color: var(--danger);
  font-size: 0.82rem;
}

.digest {
  margin: 0;
  font-size: 0.78rem;
  color: var(--ink-500);
  line-height: 1.45;
}

.memory-head-actions {
  display: flex;
  gap: 0.3rem;
  margin-bottom: 0.5rem;
}

.memory-list {
  max-height: 180px;
  overflow: auto;
  display: grid;
  gap: 0.45rem;
}

.memory-filters {
  display: flex;
  gap: 0.3rem;
}

.memory-empty {
  margin: 0;
  font-size: 0.8rem;
}

.memory-row {
  border: 1px solid rgba(58, 91, 79, 0.16);
  border-radius: 9px;
  background: rgba(247, 252, 249, 0.9);
  padding: 0.45rem;
  display: grid;
  gap: 0.35rem;
}

.memory-text {
  margin: 0;
  font-size: 0.8rem;
  line-height: 1.45;
  color: var(--ink-700);
}

.memory-actions {
  display: flex;
  gap: 0.35rem;
}

.tiny-btn {
  border: 1px solid rgba(56, 90, 77, 0.24);
  background: rgba(252, 255, 253, 0.95);
  color: var(--ink-600);
  border-radius: 999px;
  font-size: 0.74rem;
  padding: 0.2rem 0.55rem;
  cursor: pointer;
}

.tiny-btn.warn {
  color: #9f3f3f;
}

.tiny-btn.active {
  background: rgba(45, 89, 75, 0.9);
  color: #eef6f2;
  border-color: transparent;
}

.export-box {
  width: 100%;
  border: 1px solid rgba(56, 92, 79, 0.22);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.93);
  padding: 0.5rem 0.62rem;
  resize: vertical;
  font-size: 0.75rem;
  color: var(--ink-600);
}

@media (max-width: 720px) {
  .companion.expanded {
    width: calc(100vw - 2rem);
    left: 1rem !important;
    right: 1rem;
  }
  
  .pet-avatar {
    width: 80px;
    height: 80px;
  }
  
  .pet-body {
    width: 80px;
    height: 80px;
  }
  
  .pet-ears {
    gap: 46px;
  }
  
  .ear {
    width: 24px;
    height: 28px;
  }
}
</style>

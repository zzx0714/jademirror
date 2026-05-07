<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import ChatBubble from '@/components/ChatBubble.vue'
import { useApiStore } from '@/stores/apiStore'
import { useUserStore } from '@/stores/userStore'
import { useVoiceStore } from '@/stores/voiceStore'

const userStore = useUserStore()
const apiStore = useApiStore()
const voiceStore = useVoiceStore()
const router = useRouter()

const jade = computed(() => userStore.matchedJade)
const messages = ref([])
const draft = ref('')
const listRef = ref(null)
const autoSpeakReply = ref(true)
const holdButtonPressed = ref(false)

const suggestedQuestions = [
  '你经历过什么？',
  '你如何理解人心？',
  '你对当下有什么看法？',
  '你见过最美的时刻是什么？',
  '如何在浮躁中找到自己？',
]

const chatStorageKey = computed(() => {
  if (!jade.value) {
    return ''
  }
  return `jademirror-chat-${jade.value.id}`
})

const openingIntroPrompt =
  '请先用第一人称做一段简短自我介绍：你是哪一件玉、来自哪个朝代、你的性格与经历，并邀请我继续提问。'

function createAssistantGreeting() {
  if (!jade.value) {
    return '你好，我在这里。'
  }
  return `我是${jade.value.dynasty}代${jade.value.name}。请将你的问题交给我，我会以玉之记忆回应。`
}

function persistMessages() {
  if (!chatStorageKey.value) {
    return
  }
  sessionStorage.setItem(chatStorageKey.value, JSON.stringify(messages.value))
}

function scrollToBottom() {
  nextTick(() => {
    if (listRef.value) {
      listRef.value.scrollTop = listRef.value.scrollHeight
    }
  })
}

const voiceStatusText = computed(() => {
  if (!voiceStore.recognitionSupported) {
    return '当前浏览器不支持语音输入，可继续文字对话。'
  }
  if (voiceStore.holdListening || voiceStore.listening) {
    return '正在聆听中，请说话，松开按钮后将自动回填文本。'
  }
  if (voiceStore.speaking) {
    return '玉正在回应你，点击“停止播报”可中断。'
  }
  return '可点击“语音输入”或按住“按住说话”与玉交流。'
})

function mergeTranscriptToDraft(transcript) {
  const content = String(transcript || '').trim()
  if (!content) {
    return
  }
  draft.value = draft.value.trim() ? `${draft.value.trim()} ${content}` : content
}

function appendAssistantMessage(content) {
  const text = String(content || '').trim() || '我暂时听不清风声，请稍后再问。'
  messages.value.push({
    id: Date.now() + Math.random(),
    role: 'assistant',
    content: text,
  })
  if (autoSpeakReply.value && voiceStore.synthesisSupported) {
    voiceStore.speak(text)
  }
}

async function startVoiceInput() {
  if (apiStore.chatLoading || !jade.value) {
    return
  }

  voiceStore.stopSpeaking()
  const transcript = await voiceStore.recognizeOnce()
  mergeTranscriptToDraft(transcript)
}

function beginHoldToTalk() {
  if (apiStore.chatLoading || !jade.value || !voiceStore.recognitionSupported) {
    return
  }
  if (holdButtonPressed.value) {
    return
  }

  voiceStore.stopSpeaking()
  const started = voiceStore.startHoldListening()
  if (!started) {
    return
  }
  holdButtonPressed.value = true
}

async function endHoldToTalk() {
  if (!holdButtonPressed.value) {
    return
  }
  holdButtonPressed.value = false
  const transcript = await voiceStore.stopHoldListening()
  mergeTranscriptToDraft(transcript)
}

function stopVoiceInput() {
  voiceStore.stopListening()
}

async function createOpeningMessage() {
  if (!jade.value) {
    return
  }

  const fallback = createAssistantGreeting()

  try {
    const intro = await apiStore.chatWithJade({
      jade: jade.value,
      matchReason: userStore.matchReason,
      testAnswers: userStore.testAnswers,
      userVector: userStore.userVector,
      mbtiType: userStore.mbtiType,
      archetype: userStore.archetype,
      messages: [
        {
          role: 'user',
          content: openingIntroPrompt,
        },
      ],
    })

    messages.value = []
    appendAssistantMessage(intro || fallback)
  } catch {
    messages.value = []
    appendAssistantMessage(fallback)
  }

  persistMessages()
  scrollToBottom()
}

async function loadMessages(forceNew = false) {
  if (!chatStorageKey.value) {
    messages.value = []
    return
  }

  if (!forceNew) {
    try {
      const raw = sessionStorage.getItem(chatStorageKey.value)
      if (raw) {
        messages.value = JSON.parse(raw)
        scrollToBottom()
        return
      }
    } catch {
      // Ignore broken session cache and recreate opening message
    }
  }

  await createOpeningMessage()
}

async function restartConversation() {
  if (!chatStorageKey.value) {
    return
  }

  sessionStorage.removeItem(chatStorageKey.value)
  voiceStore.stopSpeaking()
  messages.value = []
  await loadMessages(true)
}

async function sendMessage() {
  const text = draft.value.trim()
  if (!text || apiStore.chatLoading || !jade.value) {
    return
  }

  voiceStore.stopListening()
  voiceStore.stopSpeaking()

  messages.value.push({
    id: Date.now(),
    role: 'user',
    content: text,
  })

  draft.value = ''
  persistMessages()
  scrollToBottom()

  try {
    const content = await apiStore.chatWithJade({
      jade: jade.value,
      matchReason: userStore.matchReason,
      testAnswers: userStore.testAnswers,
      userVector: userStore.userVector,
      mbtiType: userStore.mbtiType,
      archetype: userStore.archetype,
      messages: messages.value.map((item) => ({
        role: item.role,
        content: item.content,
      })),
    })

    appendAssistantMessage(content)
  } catch {
    appendAssistantMessage('我暂时听不清风声，请稍后再问。')
  }

  persistMessages()
  scrollToBottom()
}

watch(
  () => jade.value?.id,
  () => {
    loadMessages()
  },
)

onMounted(() => {
  voiceStore.init()
  if (!jade.value) {
    router.push('/test')
    return
  }
  loadMessages()
})

onBeforeUnmount(() => {
  holdButtonPressed.value = false
  voiceStore.stopListening()
  voiceStore.stopSpeaking()
})
</script>

<template>
  <section class="chat section-grid">
    <article v-if="jade" class="chat-card jade-card">
      <header class="chat-head">
        <div>
          <h2>{{ jade.name }}</h2>
          <p class="text-muted">让千年古玉以第一人称回应你的提问</p>
        </div>
        <div class="head-actions">
          <button type="button" class="jade-button secondary" @click="restartConversation">重新开场</button>
          <button type="button" class="jade-button primary" @click="router.push('/generate')">
            生成图像
          </button>
        </div>
      </header>

      <div ref="listRef" class="chat-list">
        <ChatBubble
          v-for="message in messages"
          :key="message.id"
          :role="message.role"
          :content="message.content"
        />
      </div>

      <div v-if="messages.length <= 1" class="suggestions">
        <p class="text-muted">建议话题：</p>
        <div class="suggestion-buttons">
          <button
            v-for="(q, idx) in suggestedQuestions"
            :key="idx"
            type="button"
            class="suggestion-btn"
            @click="draft = q"
          >
            {{ q }}
          </button>
        </div>
      </div>

      <footer class="composer">
        <div class="composer-main">
          <textarea
            v-model="draft"
            class="composer-input"
            rows="2"
            placeholder="向这件古玉提问，或点击语音输入..."
            @keydown.enter.exact.prevent="sendMessage"
          ></textarea>
          <p class="voice-hint text-muted">{{ voiceStatusText }}</p>
          <div class="voice-actions">
            <button
              type="button"
              class="jade-button secondary"
              :disabled="!voiceStore.recognitionSupported || apiStore.chatLoading || voiceStore.listening || voiceStore.holdListening"
              @click="startVoiceInput"
            >
              {{ voiceStore.listening ? '聆听中...' : '语音输入' }}
            </button>
            <button
              type="button"
              class="jade-button secondary hold-talk"
              :class="{ active: holdButtonPressed || voiceStore.holdListening }"
              :disabled="!voiceStore.recognitionSupported || apiStore.chatLoading"
              @pointerdown.prevent="beginHoldToTalk"
              @pointerup.prevent="endHoldToTalk"
              @pointerleave.prevent="endHoldToTalk"
              @pointercancel.prevent="endHoldToTalk"
            >
              {{ holdButtonPressed || voiceStore.holdListening ? '松开结束' : '按住说话' }}
            </button>
            <button
              type="button"
              class="jade-button secondary"
              :disabled="!voiceStore.listening && !voiceStore.holdListening"
              @click="stopVoiceInput"
            >
              停止聆听
            </button>
            <button
              type="button"
              class="jade-button secondary"
              :disabled="!voiceStore.speaking"
              @click="voiceStore.stopSpeaking"
            >
              停止播报
            </button>
            <label class="auto-speak">
              <input v-model="autoSpeakReply" type="checkbox" />
              自动播报回复
            </label>
          </div>
          <div v-if="voiceStore.holdListening || voiceStore.listening" class="voice-meter" aria-hidden="true">
            <span class="meter-bar bar-1"></span>
            <span class="meter-bar bar-2"></span>
            <span class="meter-bar bar-3"></span>
            <span class="meter-bar bar-4"></span>
            <span class="meter-bar bar-5"></span>
          </div>
        </div>
        <button type="button" class="jade-button primary send-btn" :disabled="apiStore.chatLoading" @click="sendMessage">
          {{ apiStore.chatLoading ? '回复生成中...' : '发送' }}
        </button>
      </footer>

      <p v-if="voiceStore.lastError" class="error-text">{{ voiceStore.lastError }}</p>
      <p v-if="apiStore.lastError" class="error-text">{{ apiStore.lastError }}</p>
    </article>
  </section>
</template>

<style scoped>
.chat-card {
  padding: 1rem;
  display: grid;
  gap: 0.8rem;
}

.chat-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 0.8rem;
  flex-wrap: wrap;
}

.head-actions {
  display: flex;
  gap: 0.5rem;
  flex-wrap: wrap;
}

.chat-list {
  min-height: 320px;
  max-height: 56vh;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 0.65rem;
  padding: 0.3rem;
  border-radius: var(--radius-md);
  background: rgba(232, 241, 235, 0.45);
  border: 1px solid rgba(69, 100, 90, 0.18);
  box-shadow: inset 0 14px 22px rgba(249, 252, 250, 0.4);
}

.composer {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.65rem;
  align-items: start;
}

.composer-main {
  display: grid;
  gap: 0.52rem;
}

.composer-input {
  resize: vertical;
  min-height: 70px;
  border: 1px solid rgba(55, 90, 77, 0.24);
  border-radius: var(--radius-md);
  padding: 0.65rem 0.72rem;
  background: rgba(252, 253, 252, 0.92);
}

.voice-hint {
  font-size: 0.85rem;
}

.voice-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.46rem;
}

.hold-talk.active {
  background: rgba(51, 104, 86, 0.92);
  color: #eef6f2;
  border-color: transparent;
}

.voice-meter {
  height: 42px;
  border-radius: var(--radius-md);
  border: 1px solid rgba(53, 102, 84, 0.24);
  background: linear-gradient(120deg, rgba(235, 245, 239, 0.95), rgba(247, 252, 248, 0.9));
  display: flex;
  align-items: end;
  justify-content: center;
  gap: 0.32rem;
  padding: 0.35rem 0.55rem;
}

.meter-bar {
  width: 6px;
  height: 35%;
  border-radius: 999px;
  background: linear-gradient(180deg, #2a6e59, #7ab298);
  animation: meter-rise 0.9s ease-in-out infinite;
  transform-origin: bottom;
}

.bar-2 {
  animation-delay: 0.08s;
}

.bar-3 {
  animation-delay: 0.16s;
}

.bar-4 {
  animation-delay: 0.24s;
}

.bar-5 {
  animation-delay: 0.32s;
}

.auto-speak {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  border-radius: 999px;
  padding: 0.3rem 0.62rem;
  border: 1px solid rgba(56, 95, 81, 0.22);
  background: rgba(245, 250, 247, 0.9);
  color: var(--ink-700);
  font-size: 0.82rem;
}

.auto-speak input {
  accent-color: #2f6757;
}

.send-btn {
  min-width: 108px;
}

.suggestions {
  padding: 0.8rem 0.65rem;
  background: rgba(236, 244, 239, 0.4);
  border-radius: var(--radius-md);
  border: 1px dashed rgba(63, 88, 75, 0.24);
}

.suggestions p {
  margin-bottom: 0.5rem;
  font-size: 0.9rem;
}

.suggestion-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}

.suggestion-btn {
  border: 1px solid rgba(45, 82, 68, 0.32);
  background: rgba(255, 255, 255, 0.88);
  border-radius: 999px;
  padding: 0.35rem 0.72rem;
  font-size: 0.85rem;
  color: var(--ink-700);
  cursor: pointer;
  transition: all 0.2s ease;
}

.suggestion-btn:hover {
  background: rgba(220, 234, 225, 0.96);
  border-color: rgba(45, 82, 68, 0.5);
  transform: translateY(-1px);
}

.error-text {
  color: var(--danger);
}

@keyframes meter-rise {
  0% {
    transform: scaleY(0.45);
    opacity: 0.5;
  }
  50% {
    transform: scaleY(1.25);
    opacity: 1;
  }
  100% {
    transform: scaleY(0.5);
    opacity: 0.55;
  }
}

@media (max-width: 780px) {
  .composer {
    grid-template-columns: 1fr;
  }

  .send-btn {
    width: 100%;
  }
}
</style>

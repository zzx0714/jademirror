import { defineStore } from 'pinia'

const RECOGNITION_LANG = 'zh-CN'
const SPEECH_RATE = 0.96
const SPEECH_PITCH = 1
const MOOD_SPEECH_MAP = {
  calm: { rate: 0.9, pitch: 0.95 },
  comforting: { rate: 0.88, pitch: 0.92 },
  cheerful: { rate: 1.02, pitch: 1.08 },
  energetic: { rate: 1.05, pitch: 1.1 },
  contemplative: { rate: 0.86, pitch: 0.9 },
}
const PERSONA_SPEECH_MAP = {
  default: { rate: 1, pitch: 1 },
  warm: { rate: 0.96, pitch: 0.94 },
  bright: { rate: 1.03, pitch: 1.06 },
  deep: { rate: 0.9, pitch: 0.88 },
}
let holdSessionResolve = null
let holdSessionPromise = null

function readSupport() {
  if (typeof window === 'undefined') {
    return {
      recognitionSupported: false,
      synthesisSupported: false,
      RecognitionCtor: null,
    }
  }

  const RecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition || null
  return {
    recognitionSupported: Boolean(RecognitionCtor),
    synthesisSupported: typeof window.speechSynthesis !== 'undefined',
    RecognitionCtor,
  }
}

function resolveSpeechProfile(mood) {
  const key = String(mood || '').trim().toLowerCase()
  if (!key) {
    return { rate: SPEECH_RATE, pitch: SPEECH_PITCH }
  }

  if (MOOD_SPEECH_MAP[key]) {
    return MOOD_SPEECH_MAP[key]
  }

  if (['anxious', 'sad', 'tired'].includes(key)) {
    return MOOD_SPEECH_MAP.comforting
  }

  if (['happy', 'curious', 'excited'].includes(key)) {
    return MOOD_SPEECH_MAP.cheerful
  }

  return { rate: SPEECH_RATE, pitch: SPEECH_PITCH }
}

export const useVoiceStore = defineStore('voice', {
  state: () => ({
    recognition: null,
    listening: false,
    recognizing: false,
    speaking: false,
    lastTranscript: '',
    lastError: '',
    recognitionSupported: false,
    synthesisSupported: false,
    initialized: false,
    holdListening: false,
    persona: 'default',
  }),
  actions: {
    /**
     * 修正语音识别常见错误
     * 优先将玉文化相关的误识别词汇修正为正确的"玉"字
     */
    correctTranscript(text) {
      if (!text) return ''
      
      let corrected = text
      
      // 玉文化相关的常见误识别修正
      // "域" → "玉" (最常见的误识别)
      corrected = corrected.replace(/与域对话/g, '与玉对话')
      corrected = corrected.replace(/和域对话/g, '和玉对话')
      corrected = corrected.replace(/跟域对话/g, '跟玉对话')
      corrected = corrected.replace(/域对话/g, '玉对话')
      corrected = corrected.replace(/开始与域/g, '开始与玉')
      corrected = corrected.replace(/开始和域/g, '开始和玉')
      
      // 其他可能的误识别
      corrected = corrected.replace(/遇见/g, '玉')  // 在"我的玉"等语境下
      corrected = corrected.replace(/预见/g, '玉')
      corrected = corrected.replace(/御/g, '玉')
      corrected = corrected.replace(/育/g, '玉')
      
      // 生成相关
      corrected = corrected.replace(/生成域/g, '生成玉')
      corrected = corrected.replace(/生域/g, '生玉')
      corrected = corrected.replace(/生成我的域/g, '生成我的玉')
      
      // 匹配相关
      corrected = corrected.replace(/匹配域/g, '匹配玉')
      corrected = corrected.replace(/匹配的域/g, '匹配的玉')
      
      // 古玉相关
      corrected = corrected.replace(/古域/g, '古玉')
      corrected = corrected.replace(/顾域/g, '古玉')
      
      console.log('🔧 语音识别修正:')
      console.log(`   原文: "${text}"`)
      console.log(`   修正: "${corrected}"`)
      
      return corrected
    },
    init() {
      if (this.initialized) {
        return
      }

      const support = readSupport()
      this.recognitionSupported = support.recognitionSupported
      this.synthesisSupported = support.synthesisSupported

      if (support.RecognitionCtor) {
        this.recognition = new support.RecognitionCtor()
        this.recognition.lang = RECOGNITION_LANG
        this.recognition.interimResults = false
        this.recognition.maxAlternatives = 1

        this.recognition.onstart = () => {
          this.listening = true
          this.recognizing = true
          this.lastError = ''
        }

        this.recognition.onend = () => {
          this.listening = false
          this.recognizing = false
          this.holdListening = false
          if (holdSessionResolve) {
            holdSessionResolve(this.lastTranscript)
            holdSessionResolve = null
            holdSessionPromise = null
          }
        }

        this.recognition.onerror = (event) => {
          this.lastError = this.mapRecognitionError(event?.error)
          this.listening = false
          this.recognizing = false
          this.holdListening = false
          if (holdSessionResolve) {
            holdSessionResolve('')
            holdSessionResolve = null
            holdSessionPromise = null
          }
        }

        this.recognition.onresult = (event) => {
          const transcript = event?.results?.[0]?.[0]?.transcript || ''
          this.lastTranscript = this.correctTranscript(transcript.trim())
        }
      }

      this.initialized = true
    },
    mapRecognitionError(errorCode) {
      const map = {
        'no-speech': '没有识别到语音，请再试一次。',
        'audio-capture': '未检测到麦克风设备。',
        'not-allowed': '麦克风权限被拒绝，请在浏览器中允许权限。',
        network: '语音识别网络异常，请稍后重试。',
        aborted: '语音识别已中断。',
      }
      return map[errorCode] || '语音识别失败，请改用文字输入。'
    },
    async recognizeOnce() {
      this.init()
      if (!this.recognitionSupported || !this.recognition) {
        this.lastError = '当前浏览器不支持语音识别，请使用文字输入。'
        return ''
      }

      this.lastError = ''
      this.lastTranscript = ''

      return new Promise((resolve) => {
        let settled = false
        const baseOnEnd = this.recognition.onend
        const baseOnError = this.recognition.onerror
        const baseOnResult = this.recognition.onresult
        let timeoutId = null

        const cleanup = () => {
          if (timeoutId) {
            window.clearTimeout(timeoutId)
            timeoutId = null
          }
          if (!this.recognition) {
            return
          }
          this.recognition.onend = baseOnEnd
          this.recognition.onerror = baseOnError
          this.recognition.onresult = baseOnResult
        }

        const finalize = (value) => {
          if (settled) {
            return
          }
          settled = true
          cleanup()
          resolve(value)
        }

        this.recognition.onerror = (event) => {
          if (baseOnError) {
            baseOnError(event)
          }
          finalize('')
        }

        this.recognition.onend = (event) => {
          if (baseOnEnd) {
            baseOnEnd(event)
          }
          finalize(this.lastTranscript)
        }

        this.recognition.onresult = (event) => {
          const transcript = event?.results?.[0]?.[0]?.transcript || ''
          this.lastTranscript = this.correctTranscript(transcript.trim())
          finalize(this.lastTranscript)
        }

        try {
          this.recognition.start()
        } catch {
          this.lastError = '语音识别启动失败，请稍后重试。'
          cleanup()
          resolve('')
          return
        }

        timeoutId = window.setTimeout(() => {
          if (settled) {
            return
          }
          this.stopListening()
          if (!this.lastTranscript) {
            this.lastError = '识别超时，请点击麦克风重试。'
          }
          finalize(this.lastTranscript)
        }, 9000)
      })
    },
    startHoldListening() {
      this.init()
      if (!this.recognitionSupported || !this.recognition) {
        this.lastError = '当前浏览器不支持语音识别，请使用文字输入。'
        return false
      }

      if (this.listening || this.holdListening) {
        return false
      }

      this.lastError = ''
      this.lastTranscript = ''
      this.holdListening = true
      holdSessionPromise = new Promise((resolve) => {
        holdSessionResolve = resolve
      })

      try {
        this.recognition.start()
        return true
      } catch {
        this.holdListening = false
        holdSessionResolve = null
        holdSessionPromise = null
        this.lastError = '语音识别启动失败，请稍后重试。'
        return false
      }
    },
    async stopHoldListening() {
      if (!this.holdListening || !this.recognition) {
        return ''
      }

      const pending = holdSessionPromise || Promise.resolve(this.lastTranscript)
      this.stopListening()
      const transcript = await pending
      this.holdListening = false
      return transcript
    },
    stopListening() {
      if (!this.recognition) {
        return
      }
      try {
        this.recognition.stop()
      } catch {
        // ignore stop race
      }
    },
    stopSpeaking() {
      if (!this.synthesisSupported || typeof window === 'undefined') {
        return
      }
      window.speechSynthesis.cancel()
      this.speaking = false
    },
    speak(text) {
      this.speakWithMood(text, '')
    },
    setPersona(persona) {
      const key = String(persona || '').trim().toLowerCase()
      if (!PERSONA_SPEECH_MAP[key]) {
        this.persona = 'default'
        return
      }
      this.persona = key
    },
    speakWithMood(text, mood = '') {
      this.init()
      if (!this.synthesisSupported || typeof window === 'undefined') {
        return
      }

      const content = String(text || '').trim()
      if (!content) {
        return
      }

      this.stopSpeaking()

      const utter = new SpeechSynthesisUtterance(content)
      utter.lang = RECOGNITION_LANG
      const profile = resolveSpeechProfile(mood)
      const personaProfile = PERSONA_SPEECH_MAP[this.persona] || PERSONA_SPEECH_MAP.default
      utter.rate = Math.max(0.6, Math.min(1.3, profile.rate * personaProfile.rate))
      utter.pitch = Math.max(0.6, Math.min(1.4, profile.pitch * personaProfile.pitch))

      utter.onstart = () => {
        this.speaking = true
      }

      utter.onend = () => {
        this.speaking = false
      }

      utter.onerror = () => {
        this.speaking = false
        this.lastError = '语音播报失败，请稍后重试。'
      }

      window.speechSynthesis.speak(utter)
    },
  },
})

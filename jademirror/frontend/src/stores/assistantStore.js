import { defineStore } from 'pinia'
import {
  clearAssistantMemories,
  deleteAssistantMemory,
  exportAssistantMemories,
  fetchAssistantMemories,
  pinAssistantMemory,
  requestAssistantProactive,
  requestAssistantTurn,
} from '@/api/jadeApi'
import { fetchJadeLibrary } from '@/api/jadeLibrary'
import { quickTestQuestions } from '@/data/questions'
import { useApiStore } from '@/stores/apiStore'
import { useUserStore } from '@/stores/userStore'
import { useVoiceStore } from '@/stores/voiceStore'
import { createFallbackJadeDataURL, urlToDataURL } from '@/utils/image'
import { computeUserVector, matchJadeByVector } from '@/utils/matching'
import { buildImagePrompt } from '@/utils/prompt'

const IDLE_NUDGE_MS = 1000 * 90

function normalizeStage(routeName) {
  const map = {
    Home: 'home', Test: 'test', Result: 'result', Chat: 'chat',
    Generate: 'generate', Gallery: 'gallery', Login: 'login',
  }
  return map[routeName] || 'home'
}

function clampWorkIndex(rawIndex, total) {
  const n = Number(rawIndex)
  if (!Number.isFinite(n) || total <= 0) return 0
  return Math.max(0, Math.min(total - 1, Math.floor(n) - 1))
}

export const useAssistantStore = defineStore('assistant', {
  state: () => ({
    ready: false,
    busy: false,
    open: false,
    autoSpeak: true,
    autoGuide: true,
    privacyMode: false,
    voicePersona: 'default',
    stage: 'home',
    lastError: '',
    messages: [],
    idleEnabled: true,
    idleTimerId: 0,
    idleNudgeCount: 0,
    emotionalTone: 'calm',
    lastMemoryDigest: '',
    memories: [],
    memoryFilter: 'all',
    memoryExportText: '',
    memoryLoading: false,
    galleryTourWorks: [],
    galleryTourIndex: -1,
    galleryTourAuto: false,
    galleryTourTimerId: 0,
    guidedTestActive: false,
    guidedQuestionIndex: 0,
  }),
  getters: {
    latestReply: (state) => {
      for (let i = state.messages.length - 1; i >= 0; i -= 1) {
        if (state.messages[i].role === 'assistant') return state.messages[i]
      }
      return null
    },
    currentQuestion: (state) =>
      state.guidedTestActive ? quickTestQuestions[state.guidedQuestionIndex] : null,
    filteredMemories: (state) => {
      if (state.memoryFilter === 'all') return state.memories
      return state.memories.filter((item) => item.memory_type === state.memoryFilter)
    },
    memoryTypeCounts: (state) => {
      const counts = { all: state.memories.length, preference: 0, emotion: 0 }
      for (const item of state.memories) {
        if (item.memory_type === 'preference') counts.preference += 1
        else if (item.memory_type === 'emotion') counts.emotion += 1
      }
      return counts
    },
  },
  actions: {
    // ═══════════════════════════════════════════
    // Stage & Context
    // ═══════════════════════════════════════════
    setStage(routeName) { this.stage = normalizeStage(routeName); this.touchActivity() },

    /** Build full-context snapshot — AI sees everything to make informed decisions */
    buildAgentContext() {
      const userStore = useUserStore()
      const j = userStore.matchedJade

      // ── Tool definitions with clear descriptions ──
      const toolDefinitions = {
        navigate: {
          name: 'navigate',
          description: '跳转到指定页面',
          params: { route: 'string - 目标路由: /test(测试页) | /result(结果页) | /chat(对话页) | /generate(生成页) | /gallery(展厅) | /home(首页)' },
          when_to_use: '用户明确要求去某个页面，如"去测试""去展厅""回首页"；或说"与玉对话""聊聊""对话"（跳转到/chat）',
          examples: [
            '去测试 → navigate({route: "/test"})', 
            '去展厅看看 → navigate({route: "/gallery"})',
            '与玉对话 → navigate({route: "/chat"})',
            '聊聊 → navigate({route: "/chat"})',
            '跟玉聊天 → navigate({route: "/chat"})'
          ]
        },
        start_guided_test: {
          name: 'start_guided_test',
          description: '开始语音引导的照心测试，AI会逐题播报问题并听取用户答案',
          params: {},
          when_to_use: '用户说"开始测试""做测试""测一测"等，且当前不在测试进行中',
          examples: ['开始测试 → start_guided_test() + navigate({route: "/test"})']
        },
        finish_test: {
          name: 'finish_test',
          description: '完成测试，计算匹配结果并展示匹配的古玉',
          params: {},
          when_to_use: '测试题目全部回答完毕，或用户说"完成测试""看结果"',
          examples: ['看结果 → finish_test()']
        },
        generate_jade: {
          name: 'generate_jade',
          description: '根据测试结果和用户情绪生成专属玉图像（需要先完成测试）',
          params: {},
          when_to_use: '用户说"生成玉""生成我的玉""生成图片"等，且已完成测试',
          examples: ['生成我的玉 → generate_jade()']
        },
        save_to_gallery: {
          name: 'save_to_gallery',
          description: '保存当前生成的专属玉到个人展厅',
          params: {},
          when_to_use: '用户说"保存""保存到展厅""收藏"等，且已生成图片',
          examples: ['保存到展厅 → save_to_gallery()']
        },
        start_gallery_tour: {
          name: 'start_gallery_tour',
          description: '开始展厅语音导览，AI会逐件介绍用户收藏的作品',
          params: {},
          when_to_use: '用户说"导览""介绍展厅""讲解作品"等',
          examples: ['导览展厅 → start_gallery_tour()']
        },
        delete_gallery_work: {
          name: 'delete_gallery_work',
          description: '删除展厅中的某件作品',
          params: { index: 'number - 作品序号（从1开始）' },
          when_to_use: '用户说"删除第X件""删除作品"等',
          examples: ['删除第2件 → delete_gallery_work({index: 2})']
        },
        open_gallery_work: {
          name: 'open_gallery_work',
          description: '详细介绍展厅中的某件作品',
          params: { index: 'number - 作品序号（从1开始）' },
          when_to_use: '用户说"看第X件""介绍第X个"等',
          examples: ['看第1件 → open_gallery_work({index: 1})']
        },
        ask_clarification: {
          name: 'ask_clarification',
          description: '向用户询问更多信息以明确意图（当用户意图不明确时使用）',
          params: { question: 'string - 要问用户的问题' },
          when_to_use: '用户的请求模糊不清，需要更多信息才能决定',
          examples: ['用户说"测试" → ask_clarification({question: "你是想开始照心测试吗？"})']
        },
        none: {
          name: 'none',
          description: '不执行任何工具，纯文本回复（用于闲聊、回答问题等）',
          params: {},
          when_to_use: '用户在闲聊、问问题、聊玉文化等，不需要执行任何操作',
          examples: ['为什么古人喜欢玉？ → none (纯聊天)']
        }
      }

      // ── System instructions embedded in context (AI reads this as prompt supplement) ──
      const instructions = `你是玉灵童子，一个玉文化AI管家。你必须遵守以下规则：

【核心原则】
1. 根据用户说的话和当前页面状态，智能决定是否需要调用工具
2. 如果用户意图明确，直接调用对应工具
3. 如果用户意图不明确，使用 ask_clarification 询问
4. 如果只是闲聊，使用 none，不调用任何工具

【决策流程】
第1步：理解用户意图
- "开始测试" → 明确，调用 start_guided_test
- "测试" → 不明确，调用 ask_clarification 询问
- "与玉对话" / "聊聊" / "对话" → 明确，调用 navigate 跳转到 /chat
- "为什么古人喜欢玉" → 闲聊，调用 none

第2步：检查前置条件
- 生成玉 → 需要先完成测试，如果没完成就提示
- 保存到展厅 → 需要先生成图片，如果没生成就提示
- 与玉对话 → 需要先完成测试并有匹配结果，如果没完成就提示

第3步：返回工具调用
- 可以一次返回多个工具，如 [navigate, start_guided_test]
- 没有操作时返回空数组 []

【返回格式】
{
  "reply": "你的自然语言回复（30-100字，口语化、像朋友聊天）",
  "tool_calls": [
    {"name": "工具名", "args": {...}}
  ]
}

【重要】
- 回复中的数据必须来自 context，不能编造
- 如果 context.test.questions 有题目，必须用那些题目
- 不要自己发明题目、玉器名称等数据`

      // ── Full test data ──
      const testData = {
        questions: quickTestQuestions.map((q, i) => ({
          index: i + 1,
          id: q.id,
          title: q.title,
          options: q.options.map((o) => ({ value: o.value, label: o.label, description: o.description })),
        })),
        total: quickTestQuestions.length,
        guided_active: this.guidedTestActive,
        current_index: this.guidedTestActive ? this.guidedQuestionIndex : -1,
        answered_count: Object.keys(userStore.testAnswers || {}).length,
      }

      // ── Jade info ──
      const jadeInfo = j ? {
        name: j.name, dynasty: j.dynasty, description: j.description,
        personality: j.personality || '',
        mbti_type: userStore.mbtiType || '',
        archetype: userStore.archetype?.label || '',
        has_matched: true,
      } : { has_matched: false }

      // ── Generate state ──
      const generateState = {
        has_generated_image: Boolean(userStore.generatedImageDataUrl),
      }

      // ── Gallery state ──
      const galleryState = {
        works_count: userStore.works.length,
        works: userStore.works.map((w, i) => ({
          index: i + 1, id: w.id, jadeName: w.jadeName, jadeDynasty: w.jadeDynasty, emotion: w.emotion,
        })),
        tour_active: this.galleryTourAuto,
      }

      // ── Available tools based on current stage ──
      const availableTools = ['navigate', 'ask_clarification', 'none']
      switch (this.stage) {
        case 'home': 
          availableTools.push('start_guided_test')
          break
        case 'test': 
          availableTools.push('start_guided_test', 'finish_test')
          break
        case 'result': 
          availableTools.push('generate_jade')
          break
        case 'chat': 
          availableTools.push('generate_jade')
          break
        case 'generate': 
          availableTools.push('generate_jade', 'save_to_gallery')
          break
        case 'gallery': 
          availableTools.push('start_gallery_tour', 'delete_gallery_work', 'open_gallery_work')
          break
      }

      return {
        instructions,
        stage: this.stage,
        current_page: this.stage,
        tool_definitions: toolDefinitions,
        available_tools: availableTools,
        test: testData,
        jade: jadeInfo,
        generate: generateState,
        gallery: galleryState,
        user_emotion: userStore.currentEmotion || 'neutral',
        idle_nudge_count: this.idleNudgeCount,
        privacy_mode: this.privacyMode,
      }
    },

    /** Legacy context for backward compatibility */
    buildContext() { return this.buildAgentContext() },

    // ═══════════════════════════════════════════
    // Tool Registry & Execution
    // ═══════════════════════════════════════════
    toolDefinitions() {
      return {
        navigate: { description: '导航到指定页面', params: { route: '目标路由，如 /test, /result, /chat, /generate, /gallery, /home' } },
        none: { description: '无需执行任何工具，仅文本回复', params: {} },
        start_guided_test: { description: '开始语音引导照心测试，玉灵童子逐题播报并听取用户答案', params: {} },
        finish_test: { description: '完成测试，计算匹配结果并展示', params: {} },
        generate_jade: { description: '根据测试结果生成专属玉图像', params: {} },
        save_to_gallery: { description: '保存当前专属玉到展厅', params: {} },
        start_gallery_tour: { description: '开始展厅语音导览', params: {} },
        delete_gallery_work: { description: '删除展厅中的某件作品', params: { index: '作品序号（从1开始）' } },
        open_gallery_work: { description: '详细介绍展厅中的某件作品', params: { index: '作品序号（从1开始）' } },
      }
    },

    async executeToolCalls(toolCalls, router) {
      console.log('🎬 开始执行工具调用列表:', toolCalls)
      console.log('📍 当前router对象:', router)
      console.log('📍 router类型:', typeof router)
      console.log('📍 router.push存在:', !!(router && router.push))
      
      if (!Array.isArray(toolCalls) || toolCalls.length === 0) {
        console.log('⚠️ 工具调用列表为空或不是数组')
        return
      }
      
      for (const call of toolCalls) {
        const name = String(call?.name || '').trim()
        const args = call?.args || {}
        console.log(`\n📌 准备执行工具 #${toolCalls.indexOf(call) + 1}: ${name}`)
        await this.executeSingleTool(name, args, router)
      }
      
      console.log('✅ 所有工具执行完毕\n')
    },

    async executeSingleTool(name, args, router) {
      console.log(`🔧 执行工具: ${name}`)
      console.log(`   参数:`, args)
      console.log(`   router存在: ${!!router}`)
      console.log(`   router.push存在: ${!!(router && router.push)}`)
      
      switch (name) {
        case 'navigate':
          if (args.route && router && router.push) {
            console.log(`🚀 准备跳转到: ${args.route}`)
            try {
              await router.push(args.route)
              console.log(`✅ 跳转成功！当前路由应该是: ${args.route}`)
            } catch (error) {
              console.error(`❌ 跳转失败:`, error)
            }
          } else {
            console.warn(`⚠️ navigate失败:`)
            console.warn(`   - route: ${args.route}`)
            console.warn(`   - router存在: ${!!router}`)
            console.warn(`   - router.push存在: ${!!(router && router.push)}`)
          }
          break
        case 'start_test':
        case 'start_guided_test':
          console.log(`🎯 启动引导测试`)
          const mode = args.mode || 'quick'  // 默认六问版
          console.log(`   - 测试模式: ${mode} (${mode === 'quick' ? '六问版' : '完整版'})`)
          
          // 设置测试模式
          const userStore = useUserStore()
          userStore.setTestMode(mode)
          console.log(`   - 已设置testMode: ${userStore.testMode}`)
          
          // 激活引导测试
          this.guidedTestActive = true
          this.guidedQuestionIndex = 0
          console.log(`   - guidedTestActive: ${this.guidedTestActive}`)
          console.log(`   - guidedQuestionIndex: ${this.guidedQuestionIndex}`)
          
          // 如果不在测试页，跳转过去
          if (this.stage !== 'test' && this.autoGuide && router) {
            console.log(`   - 🚀 跳转到 /test`)
            try {
              await router.push('/test')
              console.log(`   - ✅ 已跳转到测试页`)
            } catch (error) {
              console.error(`   - ❌ 跳转失败:`, error)
            }
          } else {
            console.log(`   - 已在测试页，TestView会自动检测testMode变化`)
          }
          break
        case 'record_answer':
          console.log(`📝 记录测试答案`)
          const answer = String(args.answer || '').toUpperCase()
          console.log(`   - 答案: ${answer}`)
          console.log(`   - guidedTestActive (执行前): ${this.guidedTestActive}`)
          console.log(`   - guidedQuestionIndex (执行前): ${this.guidedQuestionIndex}`)
          
          if (!this.guidedTestActive) {
            console.warn(`   - ⚠️ 测试未激活，无法记录答案`)
            break
          }
          
          const currentQ = quickTestQuestions[this.guidedQuestionIndex]
          if (!currentQ) {
            console.warn(`   - ⚠️ 当前没有题目 (索引: ${this.guidedQuestionIndex})`)
            break
          }
          
          console.log(`   - 当前题目: ${currentQ.id}`)
          
          // 记录答案
          const userStore2 = useUserStore()
          userStore2.setAnswer(currentQ.id, answer)
          console.log(`   - ✅ 答案已记录到userStore: ${currentQ.id} = ${answer}`)
          
          // 进入下一题
          this.guidedQuestionIndex += 1
          console.log(`   - ✅ guidedQuestionIndex已递增: ${this.guidedQuestionIndex}`)
          
          // 检查是否完成所有题目
          if (this.guidedQuestionIndex >= quickTestQuestions.length) {
            console.log(`   - 🎉 所有题目已完成！(${this.guidedQuestionIndex}/${quickTestQuestions.length})`)
            // 不自动完成测试，等待用户说"看结果"或AI判断
          } else {
            console.log(`   - 📋 还有题目未完成 (${this.guidedQuestionIndex}/${quickTestQuestions.length})`)
          }
          break
        case 'finish_test':
          await this.finishGuidedTest(router)
          break
        case 'generate_jade':
          await this.generateJadeByVoice(router)
          break
        case 'save_to_gallery':
          this.saveWorkByVoice(router)
          break
        case 'start_gallery_tour': {
          const userStore = useUserStore()
          this.guideGalleryTour(userStore.works)
          if (this.autoGuide && router) router.push('/gallery')
          break
        }
        case 'delete_gallery_work':
          this.removeWorkByVoice(args.index, router)
          break
        case 'open_gallery_work':
          this.openWorkByVoice(args.index, router)
          break
        case 'ask_clarification':
          // AI asking for clarification - just speak the question, no action needed
          // The question is already in the reply, so we don't need to do anything
          break
        case 'none':
        default:
          // No action needed - pure chat
          break
      }
    },

    // ═══════════════════════════════════════════
    // Agent Turn — send context, receive AI decision, execute tools
    // ═══════════════════════════════════════════
    async sendTurn(text, router) {
      this.busy = true
      this.lastError = ''
      try {
        const data = await requestAssistantTurn({
          text,
          stage: this.stage,
          context: this.buildAgentContext(),
        })

        const reply = data.reply || '我在，继续和我说说。'
        this.applyEmotionTone(data.emotion)
        this.appendMessage('assistant', reply)
        this.speak(reply)
        this.lastMemoryDigest = data.memory_digest || this.lastMemoryDigest

        // 🔍 DEBUG: 打印接收到的数据
        console.log('=' .repeat(80))
        console.log('📥 前端接收到的数据:', JSON.stringify(data, null, 2))
        console.log('=' .repeat(80))

        // Execute tool calls from AI agent (new format) or legacy action
        if (data.tool_calls && data.tool_calls.length > 0) {
          console.log('✅ 执行 tool_calls:', data.tool_calls)
          await this.executeToolCalls(data.tool_calls, router)
          
          // 检查是否刚刚启动了引导测试
          const hasStartGuidedTest = data.tool_calls.some(call => 
            call.name === 'start_guided_test' || call.name === 'start_test'
          )
          
          // 检查是否刚刚记录了答案
          const hasRecordAnswer = data.tool_calls.some(call => 
            call.name === 'record_answer'
          )
          
          console.log(`🔍 检查工具调用:`)
          console.log(`   - hasStartGuidedTest: ${hasStartGuidedTest}`)
          console.log(`   - hasRecordAnswer: ${hasRecordAnswer}`)
          console.log(`   - guidedTestActive: ${this.guidedTestActive}`)
          console.log(`   - guidedQuestionIndex: ${this.guidedQuestionIndex}`)
          
          // 如果刚启动测试，自动播报第一题
          if (hasStartGuidedTest && this.guidedTestActive && this.guidedQuestionIndex === 0) {
            console.log('🎯 测试刚启动，准备播报第一题')
            // 等待一小段时间让页面跳转完成
            await new Promise(resolve => setTimeout(resolve, 500))
            
            const firstQ = quickTestQuestions[0]
            const qCtx = this.buildAgentContext()
            const qData = await requestAssistantTurn({
              text: `[系统指令：请用自然语言播报第1题，题目数据见 context.test.questions[0]]`,
              stage: 'test',
              context: qCtx,
            })
            const qReply = qData.reply || this.legacyBuildQuestionGuide(firstQ, 0)
            this.appendMessage('assistant', qReply)
            this.speak(qReply)
          }
          
          // 如果刚记录了答案，自动播报下一题
          if (hasRecordAnswer && this.guidedTestActive) {
            console.log('📝 答案已记录，准备播报下一题')
            console.log(`   - guidedQuestionIndex: ${this.guidedQuestionIndex}`)
            console.log(`   - quickTestQuestions.length: ${quickTestQuestions.length}`)
            
            // 检查是否还有下一题
            if (this.guidedQuestionIndex < quickTestQuestions.length) {
              console.log(`   - ✅ 还有下一题，索引: ${this.guidedQuestionIndex}`)
              await new Promise(resolve => setTimeout(resolve, 300))
              
              const nextQ = quickTestQuestions[this.guidedQuestionIndex]
              console.log(`   - 下一题数据:`, nextQ)
              
              // 使用备用方案直接播报，不依赖AI
              const qReply = this.legacyBuildQuestionGuide(nextQ, this.guidedQuestionIndex)
              console.log(`   - 📢 直接播报下一题: ${qReply}`)
              this.appendMessage('assistant', qReply)
              this.speak(qReply)
              
              /* 原来的AI播报方案（暂时禁用，使用上面的直接播报）
              const qCtx = this.buildAgentContext()
              console.log(`   - 🚀 发送请求播报第${this.guidedQuestionIndex + 1}题`)
              
              try {
                const qData = await requestAssistantTurn({
                  text: `[系统指令：请用自然语言播报第${this.guidedQuestionIndex + 1}题，题目数据见 context.test.questions[${this.guidedQuestionIndex}]]`,
                  stage: 'test',
                  context: qCtx,
                })
                console.log(`   - ✅ 收到AI回复:`, qData.reply)
                const qReply = qData.reply || this.legacyBuildQuestionGuide(nextQ, this.guidedQuestionIndex)
                this.appendMessage('assistant', qReply)
                this.speak(qReply)
              } catch (error) {
                console.error(`   - ❌ 播报下一题失败:`, error)
                // 使用备用方案
                const qReply = this.legacyBuildQuestionGuide(nextQ, this.guidedQuestionIndex)
                this.appendMessage('assistant', qReply)
                this.speak(qReply)
              }
              */
            } else {
              console.log('   - 🎉 所有题目已完成！')
              // 提示用户可以查看结果
              const finishMsg = '太棒了！所有题目都答完啦～想看看你匹配到哪件古玉吗？说"看结果"我就帮你算～'
              this.appendMessage('assistant', finishMsg)
              this.speak(finishMsg)
            }
          } else {
            if (hasRecordAnswer) {
              console.log('⚠️ hasRecordAnswer=true 但条件不满足:')
              console.log(`   - guidedTestActive: ${this.guidedTestActive}`)
            }
          }
        } else if (data.next_action) {
          console.log('⚠️ 使用旧格式 next_action:', data.next_action)
          await this.executeSingleTool(data.next_action, data.action_payload || {}, router)
        } else {
          console.log('ℹ️ 没有工具调用，纯聊天')
        }

        // Follow suggested route
        if (data.suggested_route && this.autoGuide && router) {
          router.push(data.suggested_route)
        }
      } catch (error) {
        this.lastError = error.message || '玉灵童子暂时无法回应。'
        this.appendMessage('assistant', '我刚刚有些分神了。你可以再说一次，我会继续带着你前行。')
        this.speak('我刚刚有些分神了。你可以再说一次，我会继续带着你前行。')
      } finally {
        this.busy = false
        this.touchActivity(router)
      }
    },

    // ═══════════════════════════════════════════
    // Guided Test — AI-driven when backend supports tool_calls, legacy fallback otherwise
    // ═══════════════════════════════════════════
    async handleGuidedAnswer(answerText, router) {
      const userStore = useUserStore()
      const question = this.currentQuestion
      if (!question) { this.guidedTestActive = false; return }

      this.busy = true
      try {
        // Send answer to AI with full test context; AI should decide what to do
        const data = await requestAssistantTurn({
          text: answerText,
          stage: 'test',
          context: {
            ...this.buildAgentContext(),
            guided_answer: true,
          },
        })

        // New agent-driven path: AI returns tool_calls
        if (data.tool_calls && data.tool_calls.length > 0) {
          const reply = data.reply || '收到你的回答。'
          this.applyEmotionTone(data.emotion)
          this.appendMessage('assistant', reply)
          this.speak(reply)

          const hasRecordAnswer = data.tool_calls.some(call => call.name === 'record_answer')

          for (const call of data.tool_calls) {
            await this.executeSingleTool(call.name, call.args || {}, router)
          }

          // Auto-advance: speak next question after record_answer
          if (hasRecordAnswer && this.guidedTestActive) {
            if (this.guidedQuestionIndex < quickTestQuestions.length) {
              await new Promise(resolve => setTimeout(resolve, 300))
              const nextQ = quickTestQuestions[this.guidedQuestionIndex]
              const qReply = this.legacyBuildQuestionGuide(nextQ, this.guidedQuestionIndex)
              this.appendMessage('assistant', qReply)
              this.speak(qReply)
            } else {
              const finishMsg = '太棒了！所有题目都答完啦～想看看你匹配到哪件古玉吗？说"看结果"我就帮你算～'
              this.appendMessage('assistant', finishMsg)
              this.speak(finishMsg)
            }
          }
          return
        }

        // Legacy path: AI reply + next_action; frontend does the matching
        const reply = data.reply || '收到你的回答。'
        this.applyEmotionTone(data.emotion)
        this.appendMessage('assistant', reply)
        this.speak(reply)

        if (data.next_action === 'finish_test') {
          await this.finishGuidedTest(router)
          return
        }

        // Legacy fallback: frontend matches answer against options and advances
        const option = this.legacyPickOption(question, answerText)
        if (!option) {
          const optionsText = question.options.map((o) => o.label).join('、')
          const retry = `我还没听懂你的选择。${question.title}。你可以回答：${optionsText}。`
          this.appendMessage('assistant', retry)
          this.speak(retry)
          return
        }

        userStore.setAnswer(question.id, option.value)
        this.guidedQuestionIndex += 1

        if (this.guidedQuestionIndex >= quickTestQuestions.length) {
          await this.finishGuidedTest(router)
          return
        }

        // Ask next question via API so AI can generate natural phrasing
        const nextCtx = this.buildAgentContext()
        const nextData = await requestAssistantTurn({
          text: `[用户已回答第${this.guidedQuestionIndex}题，选择了"${option.label}"。请播报第${this.guidedQuestionIndex + 1}题。]`,
          stage: 'test',
          context: nextCtx,
        })
        const nextReply = nextData.reply || this.legacyBuildQuestionGuide(quickTestQuestions[this.guidedQuestionIndex], this.guidedQuestionIndex)
        this.appendMessage('assistant', nextReply)
        this.speak(nextReply)
      } catch {
        this.appendMessage('assistant', '我刚刚有些分神了，请再说一次你的选择。')
        this.speak('我刚刚有些分神了，请再说一次你的选择。')
      } finally {
        this.busy = false
        this.touchActivity(router)
      }
    },

    // Legacy helpers (used when AI doesn't support tool_calls)
    legacyPickOption(question, text) {
      const source = String(text || '').trim().toLowerCase()
      return question.options.find((o) => {
        const keywords = [o.value, o.label, o.description].map((s) => String(s || '').trim().toLowerCase()).filter(Boolean)
        return keywords.some((k) => source.includes(k))
      }) || null
    },
    legacyBuildQuestionGuide(question, index) {
      const optionsText = question.options.map((o) => o.label).join('、')
      return `第${index + 1}题：${question.title}。你可以回答：${optionsText}。`
    },

    async finishGuidedTest(router) {
      const userStore = useUserStore()
      try {
        const jades = await fetchJadeLibrary()
        const userVector = computeUserVector(quickTestQuestions, userStore.testAnswers)
        userStore.setUserVector(userVector)

        const result = matchJadeByVector({ jades, userVector })
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
        })
        userStore.clearGeneratedResult()
        this.guidedTestActive = false
        this.guidedQuestionIndex = 0
        if (this.autoGuide && router) router.push('/result')
      } catch (error) {
        this.guidedTestActive = false
        this.guidedQuestionIndex = 0
        this.lastError = error.message || '匹配失败，请稍后再试。'
        this.appendMessage('assistant', '匹配暂时失败了，我们稍后再试一次。')
        this.speak('匹配暂时失败了，我们稍后再试一次。')
      }
    },

    // ═══════════════════════════════════════════
    // User input entry point
    // ═══════════════════════════════════════════
    async handleUserText(text, router) {
      const input = String(text || '').trim()
      if (!input) return
      this.touchActivity(router)
      this.appendMessage('user', input)

      if (this.guidedTestActive) {
        await this.handleGuidedAnswer(input, router)
        return
      }

      await this.sendTurn(input, router)
    },

    // ═══════════════════════════════════════════
    // Idle & Proactive
    // ═══════════════════════════════════════════
    clearIdleTimer() { if (this.idleTimerId) { window.clearTimeout(this.idleTimerId); this.idleTimerId = 0 } },

    touchActivity(router) {
      if (typeof window === 'undefined') return
      this.clearIdleTimer()
      if (!this.idleEnabled || this.busy || this.stage === 'login') return
      this.idleTimerId = window.setTimeout(() => { this.triggerIdleNudge(router) }, IDLE_NUDGE_MS)
    },

    async triggerIdleNudge(router) {
      if (!this.idleEnabled || this.busy || this.stage === 'login') { this.touchActivity(router); return }
      this.busy = true; this.lastError = ''
      try {
        const data = await requestAssistantProactive({ stage: this.stage, context: this.buildAgentContext() })
        const reply = data.reply || '我在这里，想继续哪一步，我都陪你。'
        this.applyEmotionTone(data.emotion); this.appendMessage('assistant', reply); this.speak(reply)
        this.lastMemoryDigest = data.memory_digest || this.lastMemoryDigest; this.idleNudgeCount += 1
        if (data.tool_calls) await this.executeToolCalls(data.tool_calls, router)
        if (this.autoGuide && data.suggested_route && router) router.push(data.suggested_route)
      } catch (error) { this.lastError = error.message || '主动关怀触发失败。' }
      finally { this.busy = false; this.touchActivity(router) }
    },

    // ═══════════════════════════════════════════
    // Welcome
    // ═══════════════════════════════════════════
    welcomeIfNeeded() {
      if (this.ready) return
      // 标记为已准备，但不自动播报欢迎语
      this.ready = true
      this.loadMemories()
      this.touchActivity()
    },

    // ═══════════════════════════════════════════
    // Voice input
    // ═══════════════════════════════════════════
    async listenAndHandle(router) {
      const voiceStore = useVoiceStore(); voiceStore.init(); this.lastError = ''
      const transcript = await voiceStore.recognizeOnce()
      if (!transcript) { if (voiceStore.lastError) this.lastError = voiceStore.lastError; this.touchActivity(router); return }
      await this.handleUserText(transcript, router)
    },

    // ═══════════════════════════════════════════
    // Speech helpers
    // ═══════════════════════════════════════════
    appendMessage(role, content) {
      this.messages.push({ id: `${Date.now()}-${Math.random().toString(16).slice(2)}`, role, content: String(content || '') })
      if (this.messages.length > 50) this.messages = this.messages.slice(-50)
      this.touchActivity()
    },
    speak(text) {
      if (!this.autoSpeak) return
      const vs = useVoiceStore(); vs.init(); vs.setPersona(this.voicePersona)
      if (vs.synthesisSupported) vs.speakWithMood(text, this.emotionalTone)
    },
    applyEmotionTone(emotion) {
      const map = { calm: 'calm', neutral: 'calm', anxious: 'comforting', sad: 'comforting', worried: 'comforting', happy: 'cheerful', excited: 'energetic', curious: 'cheerful', reflective: 'contemplative' }
      this.emotionalTone = map[String(emotion || '').trim().toLowerCase()] || 'calm'
    },
    setPrivacyMode(enabled) {
      this.privacyMode = Boolean(enabled)
      if (this.privacyMode) { this.lastMemoryDigest = ''; this.memories = [] } else this.loadMemories()
    },
    setVoicePersona(persona) {
      const allow = new Set(['default', 'warm', 'bright', 'deep'])
      this.voicePersona = allow.has(persona) ? persona : 'default'
      useVoiceStore().setPersona(this.voicePersona)
    },

    // ═══════════════════════════════════════════
    // Tool implementations
    // ═══════════════════════════════════════════
    async generateJadeByVoice(router) {
      const userStore = useUserStore(); const apiStore = useApiStore(); const jade = userStore.matchedJade
      if (!jade) {
        this.appendMessage('assistant', '我还没为你匹配到古玉，我们先完成照心测试。')
        this.speak('我还没为你匹配到古玉，我们先完成照心测试。')
        if (this.autoGuide && router) router.push('/test'); return false
      }
      const prompt = buildImagePrompt({ answers: userStore.testAnswers, jade, emotion: userStore.currentEmotion })
      this.appendMessage('assistant', '我正在为你凝练专属玉意象，请稍候。')
      this.speak('我正在为你凝练专属玉意象，请稍候。')
      try {
        const imageUrl = await apiStore.generateImage({ prompt })
        let dataUrl = imageUrl; try { dataUrl = await urlToDataURL(imageUrl) } catch { dataUrl = imageUrl }
        userStore.setGeneratedResult({ imageDataUrl: dataUrl, prompt })
        this.appendMessage('assistant', '专属玉已经生成完成。我可以继续帮你保存到展厅。')
        this.speak('专属玉已经生成完成。我可以继续帮你保存到展厅。')
        if (this.autoGuide && router) router.push('/generate'); return true
      } catch {
        const fallback = createFallbackJadeDataURL(jade.name); userStore.setGeneratedResult({ imageDataUrl: fallback, prompt })
        this.appendMessage('assistant', '网络有点拥挤，我先为你保留一版临时玉图，你可稍后再次生成。')
        this.speak('网络有点拥挤，我先为你保留一版临时玉图，你可稍后再次生成。')
        if (this.autoGuide && router) router.push('/generate'); return false
      }
    },
    saveWorkByVoice(router) {
      const userStore = useUserStore(); const work = userStore.saveCurrentWork()
      if (!work) { this.appendMessage('assistant', '还没有可保存的专属玉。'); this.speak('还没有可保存的专属玉。'); return false }
      this.appendMessage('assistant', `已帮你保存到展厅：${work.jadeDynasty}代意象的${work.jadeName}。`)
      this.speak(`已帮你保存到展厅：${work.jadeDynasty}代意象的${work.jadeName}。`)
      if (this.autoGuide && router) router.push('/gallery'); return true
    },
    removeWorkByVoice(index, router) {
      const userStore = useUserStore()
      if (!userStore.works.length) { this.appendMessage('assistant', '展厅目前没有藏品可删除。'); this.speak('展厅目前没有藏品可删除。'); return false }
      const ti = clampWorkIndex(index, userStore.works.length); const target = userStore.works[ti]
      userStore.removeWork(target.id)
      this.appendMessage('assistant', `已删除第${ti + 1}件作品：${target.jadeName}。`); this.speak(`已删除第${ti + 1}件作品：${target.jadeName}。`)
      if (this.autoGuide && router) router.push('/gallery'); return true
    },
    openWorkByVoice(index, router) {
      const userStore = useUserStore()
      if (!userStore.works.length) { this.appendMessage('assistant', '展厅还没有作品。'); this.speak('展厅还没有作品。'); return false }
      const ti = clampWorkIndex(index, userStore.works.length); const work = userStore.works[ti]
      const text = this.composeWorkNarration(work, ti, userStore.works.length)
      this.appendMessage('assistant', text); this.speak(text)
      if (this.autoGuide && router) router.push('/gallery'); return true
    },

    // ═══════════════════════════════════════════
    // Gallery Tour
    // ═══════════════════════════════════════════
    composeWorkNarration(work, index, total) {
      return `第${index + 1}件，共${total}件。${work.jadeDynasty}代意象，${work.jadeName}。情绪基调：${work.emotion || '平和'}。${work.prompt ? `生成意图：${String(work.prompt).slice(0, 80)}。` : '它承载了你当时的心境与偏好。'}`
    },
    speakCurrentGalleryWork() {
      if (!this.galleryTourWorks.length || this.galleryTourIndex < 0) return
      const cur = this.galleryTourWorks[this.galleryTourIndex]
      this.appendMessage('assistant', this.composeWorkNarration(cur, this.galleryTourIndex, this.galleryTourWorks.length))
      this.speak(this.composeWorkNarration(cur, this.galleryTourIndex, this.galleryTourWorks.length))
    },
    nextGalleryWork() {
      if (!this.galleryTourWorks.length) return
      this.galleryTourIndex = (this.galleryTourIndex + 1) % this.galleryTourWorks.length
      this.speakCurrentGalleryWork()
    },
    prevGalleryWork() {
      if (!this.galleryTourWorks.length) return
      this.galleryTourIndex = (this.galleryTourIndex - 1 + this.galleryTourWorks.length) % this.galleryTourWorks.length
      this.speakCurrentGalleryWork()
    },
    stopGalleryTour() {
      this.galleryTourAuto = false
      if (this.galleryTourTimerId) { window.clearInterval(this.galleryTourTimerId); this.galleryTourTimerId = 0 }
      this.galleryTourWorks = []; this.galleryTourIndex = -1; useVoiceStore().stopSpeaking()
    },
    startAutoGalleryTour() {
      if (!this.galleryTourWorks.length) return
      this.galleryTourAuto = true
      if (this.galleryTourTimerId) window.clearInterval(this.galleryTourTimerId)
      this.galleryTourTimerId = window.setInterval(() => { this.nextGalleryWork() }, 10000)
    },
    pauseAutoGalleryTour() { this.galleryTourAuto = false; if (this.galleryTourTimerId) { window.clearInterval(this.galleryTourTimerId); this.galleryTourTimerId = 0 } },
    guideGalleryTour(works = []) {
      if (!Array.isArray(works) || works.length === 0) {
        this.appendMessage('assistant', '你的展厅还没有藏品。等你生成第一件专属玉后，我会为你做语音导览。')
        this.speak('你的展厅还没有藏品。等你生成第一件专属玉后，我会为你做语音导览。'); return
      }
      this.galleryTourWorks = [...works]; this.galleryTourIndex = 0
      this.appendMessage('assistant', `欢迎来到你的个人展厅。你目前收藏了${works.length}件玉作。现在我为你逐件讲解。`)
      this.speak(`欢迎来到你的个人展厅。你目前收藏了${works.length}件玉作。现在我为你逐件讲解。`)
      this.speakCurrentGalleryWork(); this.startAutoGalleryTour()
    },

    // ═══════════════════════════════════════════
    // Memory
    // ═══════════════════════════════════════════
    async loadMemories() {
      if (this.privacyMode) { this.memories = []; this.lastMemoryDigest = ''; this.memoryLoading = false; return }
      this.memoryLoading = true; this.lastError = ''
      try {
        const data = await fetchAssistantMemories()
        this.memories = Array.isArray(data.memories) ? data.memories : []; this.lastMemoryDigest = data.digest || this.lastMemoryDigest
      } catch (error) { this.lastError = error.message || '记忆加载失败。' }
      finally { this.memoryLoading = false }
    },
    setMemoryFilter(type) { const allow = new Set(['all', 'preference', 'emotion']); this.memoryFilter = allow.has(type) ? type : 'all' },
    async setMemoryPinned(memoryId, pinned) {
      if (this.privacyMode) return
      this.lastError = ''
      try { const data = await pinAssistantMemory(memoryId, pinned); this.memories = Array.isArray(data.memories) ? data.memories : this.memories; this.lastMemoryDigest = data.digest || this.lastMemoryDigest }
      catch (error) { this.lastError = error.message || '记忆置顶操作失败。' }
    },
    async removeMemory(memoryId) {
      if (this.privacyMode) return
      this.lastError = ''
      try { const data = await deleteAssistantMemory(memoryId); this.memories = Array.isArray(data.memories) ? data.memories : this.memories; this.lastMemoryDigest = data.digest || this.lastMemoryDigest }
      catch (error) { this.lastError = error.message || '记忆删除失败。' }
    },
    async clearAllMemories() {
      if (this.privacyMode) { this.memories = []; this.lastMemoryDigest = ''; this.memoryExportText = ''; return }
      this.lastError = ''
      try { const data = await clearAssistantMemories(); this.memories = Array.isArray(data.memories) ? data.memories : []; this.lastMemoryDigest = data.digest || ''; this.memoryExportText = '' }
      catch (error) { this.lastError = error.message || '记忆清空失败。' }
    },
    async exportMemories() {
      if (this.privacyMode) { this.memoryExportText = ''; return }
      this.lastError = ''
      try { const data = await exportAssistantMemories(); this.memoryExportText = JSON.stringify(data, null, 2) }
      catch (error) { this.lastError = error.message || '记忆导出失败。' }
    },

    // ═══════════════════════════════════════════
    // Teardown
    // ═══════════════════════════════════════════
    teardown() { this.clearIdleTimer(); this.pauseAutoGalleryTour() },
  },
})

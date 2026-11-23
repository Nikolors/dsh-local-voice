import { assistantText, splitSentencesWithOffsets } from './text.js'
import { appendFileSync } from 'node:fs'

const STREAM_LOG = process.env.DSH_TTS_STREAM_LOG || ''
const note = (text) => {
  try {
    appendFileSync(STREAM_LOG, `${new Date().toISOString()} ${text}\n`, 'utf8')
  } catch {
    /* журнал недоступен */
  }
}

export function registerSessionEvents(ctx, { live, cleanText, prepareSpeechTextBound, speakPiece, announce }) {
  const collectors = new Map()
  // 23.11: поток живой речи — копим текстовые дельты и говорим по предложениям.
  const streamBuf = new Map()
  const streamSpokenAt = new Map()
  const streamSpokenPending = new Set()

  const speakStreamPiece = (sid, raw, cfg) => {
    note(`фраза в потоке: ${raw.length} симв.`)
    prepareSpeechTextBound(raw, cfg).then((ready) => {
      if (!ready) {
        note('подготовка вернула пусто')
        return
      }
      const pieces = splitSentencesWithOffsets(ready, cfg.sentenceChars)
      for (const piece of pieces) {
        speakPiece(sid, piece.text, cfg, 'reply', {
          sentenceIndex: piece.sentenceIndex,
          totalSentences: piece.totalSentences,
          charStart: piece.charStart,
          charEnd: piece.charEnd,
        })
      }
      streamSpokenAt.set(sid, Date.now())
      streamSpokenPending.add(sid)
      note(`озвучено кусков: ${pieces.length}`)
    }).catch((error) => { note(`ошибка подготовки: ${error && error.message}`) })
  }

  ctx.effect(() => ctx.on('agent/assistant-stream', (payload) => {
    const frame = payload && payload.frame
    const kind = frame ? `${frame.type}/${frame.chunk && frame.chunk.type}` : 'нет кадра'
    note(`событие потока: ${kind}`)
    const cfg = live()
    if (!cfg.speakReplies) return
    if (!frame || !frame.attemptId) return
    const sid = String(frame.attemptId).split(':')[0]
    if (frame.type === 'chunk' && frame.chunk && frame.chunk.type === 'text-delta') {
      const text = (streamBuf.get(frame.attemptId) || '') + (frame.chunk.text || '')
      let cut = 0
      for (let i = 0; i < text.length; i += 1) {
        if ('.!?…'.includes(text[i])) {
          const next = text[i + 1]
          if (next === undefined || /\s/.test(next)) cut = i + 1
        }
      }
      if (cut > 0) {
        const ready = text.slice(0, cut).trim()
        streamBuf.set(frame.attemptId, text.slice(cut))
        if (ready.length >= 2) speakStreamPiece(sid, ready, cfg)
      } else {
        streamBuf.set(frame.attemptId, text)
      }
      return
    }
    if (frame.type === 'end') {
      const rest = (streamBuf.get(frame.attemptId) || '').trim()
      streamBuf.delete(frame.attemptId)
      if (rest.length >= 2) speakStreamPiece(sid, rest, cfg)
    }
  }))

  ctx.effect(() => ctx.on('session/event', (session, event) => {
    const cfg = live()
    if (!cfg.speakReplies) return
    const sid = session && session.id
    if (!sid) return
    const isMessenger = session && (session.kind === 'im' || session.kind === 'messenger' || session.channel === 'messenger' || session.type === 'im' || session.source === 'messenger')
    if (isMessenger && !cfg.messengerTtsEnabled) return
    const isVoice = session && (session.kind === 'voice' || session.channel === 'voice' || session.type === 'voice' || session.mode === 'voice')
    if (isVoice && !cfg.voiceDuplexEnabled) return

    // The agent is waiting for human approval — worth speaking aloud
    // so the user notices without watching the screen.
    if (event.type === 'approval/asked') {
      if (!cfg.announceApproval) return
      const tool = event.data && event.data.toolName
      announce(sid, cfg.approvalText + (tool ? ': ' + tool : ''), cfg)
      return
    }
    if (event.type === 'question/requested') {
      if (!cfg.announceApproval || !cfg.questionText) return
      announce(sid, cfg.questionText, cfg)
      return
    }

    if (event.type === 'assistant/message') {
      const text = assistantText(event.data && (event.data.message ?? event.data.text ?? event.data))
      const agentName = (event.data && (
        event.data.subagent ||
        event.data.agent ||
        (event.data.message && (event.data.message.subagent || event.data.message.agent || event.data.message.sender || (event.data.message.author && event.data.message.author.name)))
      )) || (session && (session.subagent || session.agent)) || ''
      const allowSubagent = cfg.autoDetectSubagent !== false
      const activeRole = (allowSubagent && agentName && cfg.roles && cfg.roles[agentName] && (cfg.roles[agentName].provider || cfg.roles[agentName].voice)) ? agentName : 'reply'

      // Speak as it goes: each incoming chunk is spoken immediately
      // in sentence pieces; waiting for turn end means silence while the agent works.
      if (cfg.speakAsItGoes) {
        // Поток уже проговорил этот ответ — не повторяем его целиком.
        // Флаг снимается один раз: следующий ответ озвучивается как обычно.
        if (streamSpokenPending.has(sid)) {
          streamSpokenPending.delete(sid)
          return
        }
        prepareSpeechTextBound(text, cfg).then((ready) => {
          if (!ready) return
          const pieces = splitSentencesWithOffsets(ready, cfg.sentenceChars)
          for (const piece of pieces) {
            speakPiece(sid, piece.text, cfg, activeRole, {
              sentenceIndex: piece.sentenceIndex,
              totalSentences: piece.totalSentences,
              charStart: piece.charStart,
              charEnd: piece.charEnd,
            })
          }
        }).catch((_err) => {
          // best-effort speech fallback
        })
        return
      }
      const cur = collectors.get(sid) || { parts: [], role: activeRole }
      cur.parts.push(text)
      cur.role = activeRole
      collectors.set(sid, cur)
      return
    }
    if (event.type !== 'turn/end') return
    // With speak-as-it-goes everything was already spoken by turn end.
    if (cfg.speakAsItGoes) { collectors.delete(sid); return }
    const cur = collectors.get(sid)
    collectors.delete(sid)
    const raw = cur && Array.isArray(cur.parts) ? cur.parts.join('\n') : ''
    const activeRole = (cur && cur.role) || 'reply'
    prepareSpeechTextBound(raw, cfg).then((text) => {
      if (!text) return
      const pieces = splitSentencesWithOffsets(text, cfg.sentenceChars)
      for (const piece of pieces) {
        speakPiece(sid, piece.text, cfg, activeRole, {
          sentenceIndex: piece.sentenceIndex,
          totalSentences: piece.totalSentences,
          charStart: piece.charStart,
          charEnd: piece.charEnd,
        })
      }
    }).catch((err) => {
      ctx.logger?.warn?.(`[dsh-tts] failed to prepare speech text for session ${sid}: ${err?.message || err}`)
      try {
        const fallbackText = cleanText(raw, cfg)
        if (fallbackText) {
          const pieces = splitSentencesWithOffsets(fallbackText, cfg.sentenceChars)
          for (const piece of pieces) {
            speakPiece(sid, piece.text, cfg, activeRole, {
              sentenceIndex: piece.sentenceIndex,
              totalSentences: piece.totalSentences,
              charStart: piece.charStart,
              charEnd: piece.charEnd,
            })
          }
        }
      } catch (fallbackErr) {
        ctx.logger?.error?.(`[dsh-tts] fallback speech text failed for session ${sid}: ${fallbackErr?.message || fallbackErr}`)
      }
    })
  }), 'dsh-tts: speak finished replies')
}

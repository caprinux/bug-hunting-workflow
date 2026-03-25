import React, { useState, useEffect, useRef } from 'react'
import { api } from '../utils/api'

/**
 * Renders a stream.jsonl file as a chat-style conversation view.
 *
 * - Left bubbles: agent thoughts and messages
 * - Right bubbles: orchestrator instructions (prompts, system messages)
 * - Center: tool calls, collapsed by default
 */
export default function ConversationView({ engagementId, runId, stageName, jsonlPath, onClose }) {
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const scrollRef = useRef(null)

  useEffect(() => {
    loadStream()
  }, [engagementId, runId, stageName, jsonlPath])

  async function loadStream() {
    setLoading(true)
    setError(null)
    try {
      const data = await api.getStageOutput(engagementId, runId, stageName, jsonlPath)
      if (data.content && typeof data.content === 'string') {
        const parsed = parseJsonl(data.content)
        setMessages(parsed)
      } else {
        setError('Could not load stream data')
      }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  if (loading) return <div className="loading">Loading conversation...</div>
  if (error) return <div className="error-msg">{error}</div>

  return (
    <div className="conversation-view">
      <div className="conversation-header">
        <h3>Agent Conversation</h3>
        <span className="muted">{messages.length} messages</span>
        <button className="close-btn" onClick={onClose}>Close</button>
      </div>
      <div className="conversation-body" ref={scrollRef}>
        {messages.map((msg, i) => (
          <MessageBubble key={i} msg={msg} />
        ))}
        {messages.length === 0 && (
          <div className="empty-state"><p>No messages in this stream</p></div>
        )}
      </div>
    </div>
  )
}


function MessageBubble({ msg }) {
  const [expanded, setExpanded] = useState(false)

  if (msg.role === 'system') {
    return (
      <div className="msg-row msg-right">
        <div className="msg-bubble msg-system">
          <div className="msg-label">System</div>
          <div className="msg-text">{truncate(msg.text, 300)}</div>
          <div className="msg-time">{msg.time}</div>
        </div>
      </div>
    )
  }

  if (msg.role === 'tool') {
    return (
      <div className="msg-row msg-center">
        <div className="msg-tool" onClick={() => setExpanded(!expanded)}>
          <span className="msg-tool-icon">{expanded ? '▼' : '▶'}</span>
          <span className="msg-tool-name">{msg.toolName || 'Tool Call'}</span>
          {msg.toolDesc && <span className="msg-tool-desc">{msg.toolDesc}</span>}
        </div>
        {expanded && (
          <div className="msg-tool-body">
            {msg.input && (
              <div className="msg-tool-section">
                <div className="msg-tool-section-label">Input</div>
                <pre>{typeof msg.input === 'string' ? msg.input : JSON.stringify(msg.input, null, 2)}</pre>
              </div>
            )}
            {msg.output && (
              <div className="msg-tool-section">
                <div className="msg-tool-section-label">Output</div>
                <pre>{truncate(typeof msg.output === 'string' ? msg.output : JSON.stringify(msg.output, null, 2), 2000)}</pre>
              </div>
            )}
          </div>
        )}
      </div>
    )
  }

  if (msg.role === 'thinking') {
    return (
      <div className="msg-row msg-left">
        <div className="msg-bubble msg-thinking" onClick={() => setExpanded(!expanded)}>
          <div className="msg-label">Thinking {expanded ? '▼' : '▶'}</div>
          {expanded && <div className="msg-text">{msg.text}</div>}
          {!expanded && <div className="msg-text msg-truncated">{truncate(msg.text, 100)}</div>}
        </div>
      </div>
    )
  }

  // Default: agent message (left bubble)
  return (
    <div className="msg-row msg-left">
      <div className="msg-bubble msg-agent">
        {msg.label && <div className="msg-label">{msg.label}</div>}
        <div className="msg-text">{msg.text}</div>
        <div className="msg-time">{msg.time}</div>
      </div>
    </div>
  )
}


function parseJsonl(content) {
  const lines = content.split('\n').filter(l => l.trim())
  const messages = []

  for (const line of lines) {
    try {
      const entry = JSON.parse(line)
      const time = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : ''

      if (entry.stream === 'stderr') continue

      let raw
      try {
        raw = JSON.parse(entry.raw || '{}')
      } catch {
        raw = { type: 'unknown', text: entry.raw || '' }
      }

      const type = raw.type || ''

      // System init
      if (type === 'system' && raw.subtype === 'init') {
        messages.push({
          role: 'system', time,
          text: `Session started — model: ${raw.model || '?'}, tools: ${(raw.tools || []).length}`,
        })
        continue
      }

      // Task started (subagent spawn)
      if (type === 'system' && raw.subtype === 'task_started') {
        messages.push({
          role: 'system', time,
          text: `Spawned subagent: ${raw.description || raw.task_id || '?'}`,
        })
        continue
      }

      // Task completed (subagent result)
      if (type === 'system' && raw.subtype === 'task_completed') {
        messages.push({
          role: 'tool', time,
          toolName: 'Subagent Result',
          toolDesc: raw.task_id || '',
          output: raw.result || raw.output || '',
        })
        continue
      }

      // Assistant message with content array
      if (type === 'assistant' && raw.message?.content) {
        const content = raw.message.content
        if (Array.isArray(content)) {
          for (const block of content) {
            if (block.type === 'thinking') {
              messages.push({ role: 'thinking', time, text: block.thinking || '' })
            } else if (block.type === 'text') {
              messages.push({ role: 'agent', time, text: block.text || '' })
            } else if (block.type === 'tool_use') {
              messages.push({
                role: 'tool', time,
                toolName: block.name || 'Tool',
                toolDesc: block.input?.description || block.input?.pattern || block.input?.command || '',
                input: block.input,
              })
            }
          }
        }
        continue
      }

      // Tool result
      if (type === 'result') {
        continue // Skip final result, it's in result.json
      }

      // Codex: agent_message
      if (type === 'item.completed' && raw.item?.type === 'agent_message') {
        messages.push({ role: 'agent', time, label: 'Codex', text: raw.item.text || '' })
        continue
      }

      // Codex: command execution
      if (type === 'item.completed' && raw.item?.type === 'command_execution') {
        messages.push({
          role: 'tool', time,
          toolName: 'Shell',
          toolDesc: raw.item.command || '',
          output: raw.item.aggregated_output || '',
        })
        continue
      }

      // Codex: todo list
      if (type === 'item.started' && raw.item?.type === 'todo_list') {
        const items = (raw.item.items || []).map(t => `${t.completed ? '✓' : '○'} ${t.text}`).join('\n')
        messages.push({ role: 'agent', time, label: 'Plan', text: items })
        continue
      }

      // Codex: turn started/completed
      if (type === 'turn.started' || type === 'turn.completed' || type === 'thread.started') {
        continue
      }

    } catch {
      // Skip unparseable lines
    }
  }

  return messages
}


function truncate(text, maxLen) {
  if (!text) return ''
  if (text.length <= maxLen) return text
  return text.slice(0, maxLen) + '...'
}

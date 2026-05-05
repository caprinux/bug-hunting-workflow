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

  // Start at the top of the conversation
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0
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
        {groupMessages(messages).map((item, i) => {
          if (item.type === 'group') {
            return <ToolGroup key={i} tools={item.tools} />
          }
          return <MessageBubble key={i} msg={item} />
        })}
        {messages.length === 0 && (
          <div className="empty-state"><p>No messages in this stream</p></div>
        )}
      </div>
    </div>
  )
}


/**
 * Group consecutive tool calls. If 3+ tools appear in a row,
 * collapse them into a single expandable group.
 */
function groupMessages(messages) {
  const result = []
  let toolBuffer = []

  function flushTools() {
    if (toolBuffer.length === 0) return
    if (toolBuffer.length <= 2) {
      // Few enough to show inline
      toolBuffer.forEach(t => result.push(t))
    } else {
      // Collapse into a group
      result.push({ type: 'group', tools: [...toolBuffer] })
    }
    toolBuffer = []
  }

  for (const msg of messages) {
    if (msg.role === 'tool') {
      toolBuffer.push(msg)
    } else {
      flushTools()
      result.push(msg)
    }
  }
  flushTools()
  return result
}


function ToolGroup({ tools }) {
  const [expanded, setExpanded] = useState(false)

  // Show first and last tool as preview
  const first = tools[0]
  const last = tools[tools.length - 1]
  const hidden = tools.length - 2

  return (
    <div className="msg-row msg-center">
      <div className="msg-tool-group">
        <div className="msg-tool-group-header" onClick={() => setExpanded(!expanded)}>
          <span className="msg-tool-icon">{expanded ? '▼' : '▶'}</span>
          <span className="msg-tool-name">{tools.length} tool calls</span>
          <span className="msg-tool-desc">
            {first.toolName}{first.toolDesc ? `: ${truncate(first.toolDesc, 30)}` : ''}
            {hidden > 0 && ` ... +${hidden} more ... `}
            {last !== first && (last.toolName + (last.toolDesc ? `: ${truncate(last.toolDesc, 30)}` : ''))}
          </span>
        </div>
        {expanded && (
          <div className="msg-tool-group-body">
            {tools.map((tool, i) => (
              <ToolCallInGroup key={i} msg={tool} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}


function ToolCallInGroup({ msg }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="msg-tool-group-item">
      <div className="msg-tool" onClick={() => setExpanded(!expanded)}>
        <span className="msg-tool-icon">{expanded ? '▼' : '▶'}</span>
        <span className="msg-tool-name">{msg.toolName || 'Tool'}</span>
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

      // Codex CLI format: agent_message
      if (type === 'item.completed' && raw.item?.type === 'agent_message') {
        messages.push({ role: 'agent', time, label: 'Codex', text: raw.item.text || '' })
        continue
      }

      // Codex CLI format: command execution
      if (type === 'item.completed' && raw.item?.type === 'command_execution') {
        messages.push({
          role: 'tool', time,
          toolName: 'Shell',
          toolDesc: raw.item.command || '',
          output: raw.item.aggregated_output || '',
        })
        continue
      }

      // Codex CLI format: todo list
      if (type === 'item.started' && raw.item?.type === 'todo_list') {
        const items = (raw.item.items || []).map(t => `${t.completed ? '✓' : '○'} ${t.text}`).join('\n')
        messages.push({ role: 'agent', time, label: 'Plan', text: items })
        continue
      }

      // Codex CLI format: turn started/completed
      if (type === 'turn.started' || type === 'turn.completed' || type === 'thread.started') {
        continue
      }

      // Codex agent-sdk format (current — type=codex_event, snake_case)
      if (type === 'codex_event') {
        const eventType = raw.event_type || ''
        const itemType = raw.item_type || ''
        if (eventType === 'item_completed' && itemType === 'agent_message') {
          // When the codex schema includes a `narrative` field, the assistant
          // message text is `{"narrative": "...", ...}`. Surface the narrative
          // as the visible bubble; if it is empty or the text is not JSON,
          // fall back to the raw text so we never silently drop a turn.
          const txt = raw.text || ''
          let display = txt
          let dropped = false
          try {
            const obj = JSON.parse(txt)
            if (obj && typeof obj === 'object' && 'narrative' in obj) {
              const narrative = (obj.narrative || '').trim()
              if (narrative) {
                display = narrative
              } else {
                dropped = true
              }
            }
          } catch { /* leave display = txt */ }
          if (!dropped) {
            messages.push({ role: 'agent', time, label: 'Codex', text: display })
          }
          continue
        }
        if (eventType === 'item_completed' && itemType === 'command_execution') {
          messages.push({
            role: 'tool', time,
            toolName: 'Shell',
            toolDesc: raw.command || '',
            output: raw.output || '',
          })
          continue
        }
        if (eventType === 'item_completed' && itemType === 'reasoning' && raw.text) {
          messages.push({ role: 'thinking', time, text: raw.text })
          continue
        }
        if (eventType === 'item_completed' && itemType === 'web_search') {
          messages.push({
            role: 'tool', time,
            toolName: 'WebSearch',
            toolDesc: raw.query || '',
            input: raw.query || '',
          })
          continue
        }
        if (eventType === 'item_completed' && itemType === 'error') {
          messages.push({
            role: 'system', time,
            text: `[error] ${raw.message || ''}`,
          })
          continue
        }
        if (eventType === 'item_started' && itemType === 'todo_list' && Array.isArray(raw.items)) {
          const lines = raw.items.map(t => `${t.completed ? '✓' : '○'} ${t.text}`).join('\n')
          if (lines) messages.push({ role: 'agent', time, label: 'Plan', text: lines })
          continue
        }
        if (eventType === 'item_completed' && itemType === 'file_change' && Array.isArray(raw.changes)) {
          const lines = raw.changes.map(c => `${c.kind || 'change'}: ${c.path}`).join('\n')
          if (lines) messages.push({
            role: 'tool', time,
            toolName: 'FileChange', toolDesc: raw.changes[0]?.path || '',
            output: lines,
          })
          continue
        }
        // item_started for non-todo, thread_started, turn_started, turn_completed,
        // turn_failed, etc.: ignore.
        continue
      }

      // Codex app-server SDK format (method-based, camelCase)
      const method = raw.method || ''
      const params = raw.params || {}
      const sdkItem = params.item || {}

      if (method === 'item/completed' && sdkItem.type === 'agentMessage') {
        messages.push({ role: 'agent', time, label: 'Codex', text: sdkItem.text || '' })
        continue
      }
      if (method === 'item/completed' && sdkItem.type === 'commandExecution') {
        messages.push({
          role: 'tool', time,
          toolName: 'Shell',
          toolDesc: sdkItem.command || '',
          output: sdkItem.aggregatedOutput || '',
        })
        continue
      }
      if (method === 'item/completed' && sdkItem.type === 'reasoning' && sdkItem.content?.length) {
        messages.push({ role: 'thinking', time, text: typeof sdkItem.content === 'string' ? sdkItem.content : JSON.stringify(sdkItem.content) })
        continue
      }
      if (method === 'turn/started' || method === 'turn/completed' || method === 'thread/status/changed') {
        continue
      }

      // Tool use (Claude SDK normalized format)
      if (type === 'tool_use') {
        messages.push({
          role: 'tool', time,
          toolName: raw.name || 'Tool',
          toolDesc: raw.input?.command || raw.input?.file_path || raw.input?.pattern || '',
          input: raw.input,
        })
        continue
      }

      // Tool result (Claude SDK normalized format)
      if (type === 'tool_result') {
        if (messages.length > 0 && messages[messages.length - 1].role === 'tool') {
          messages[messages.length - 1].output = raw.content || ''
        }
        continue
      }

      // Thinking block (Claude SDK normalized format)
      if (type === 'content_block_delta' && raw.delta?.type === 'thinking_delta') {
        messages.push({ role: 'thinking', time, text: raw.delta.thinking || '' })
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

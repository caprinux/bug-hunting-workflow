import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useParams, useSearchParams, Link } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../utils/api'
import { useWebSocket } from '../hooks/useWebSocket'
import useTitle from '../hooks/useTitle'

export default function Chat() {
  const { id: engagementId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const activeChatId = searchParams.get('chat')

  const [engagement, setEngagement] = useState(null)
  useTitle(engagement ? `Chat — ${engagement.name}` : 'Chat')
  const [chats, setChats] = useState([])
  const [messages, setMessages] = useState([])
  const [files, setFiles] = useState([])
  const [showFiles, setShowFiles] = useState(false)
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [thinkingText, setThinkingText] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const { events, connected } = useWebSocket(engagementId)
  const messagesEndRef = useRef(null)
  const inputRef = useRef(null)
  const lastProcessedEvent = useRef(0)

  // Load engagement info
  useEffect(() => {
    api.getEngagement(engagementId).then(setEngagement).catch(console.error)
  }, [engagementId])

  // Load chat list
  const loadChats = useCallback(async () => {
    try {
      const data = await api.listChats(engagementId)
      setChats(data)
    } catch (e) {
      console.error('Failed to load chats:', e)
    }
    setLoading(false)
  }, [engagementId])

  useEffect(() => { loadChats() }, [loadChats])

  const loadFiles = useCallback(async () => {
    try {
      const data = await api.listChatFiles(engagementId)
      setFiles(data.files || [])
    } catch { setFiles([]) }
  }, [engagementId])

  // Reload files after each chat completion
  useEffect(() => { if (showFiles) loadFiles() }, [showFiles, loadFiles])

  // Load messages when active chat changes, and check if backend is still streaming
  useEffect(() => {
    if (!activeChatId) {
      setMessages([])
      return
    }
    setStreamingText('')
    setThinkingText('')
    setStreaming(false)
    api.getChat(engagementId, activeChatId)
      .then(data => setMessages(data.messages || []))
      .catch(e => {
        console.error('Failed to load chat:', e)
        setMessages([])
      })
    // Check if backend has an active stream for this chat (e.g. after page reload)
    api.chatStreamingStatus(engagementId, activeChatId)
      .then(data => {
        if (data.streaming) setStreaming(true)
      })
      .catch(() => {})
  }, [engagementId, activeChatId])

  // Poll for completion while streaming but no WebSocket events are arriving (e.g. after reload)
  useEffect(() => {
    if (!streaming || !activeChatId) return
    const interval = setInterval(async () => {
      try {
        const data = await api.chatStreamingStatus(engagementId, activeChatId)
        if (!data.streaming) {
          setStreaming(false)
          setStreamingText('')
          setThinkingText('')
          // Reload messages to get the completed response
          const chat = await api.getChat(engagementId, activeChatId)
          setMessages(chat.messages || [])
          loadFiles()
        }
      } catch {}
    }, 2000)
    return () => clearInterval(interval)
  }, [streaming, activeChatId, engagementId, loadFiles])

  // Process WebSocket events for chat streaming
  useEffect(() => {
    if (events.length === 0) return
    const newEvents = events.slice(lastProcessedEvent.current)
    lastProcessedEvent.current = events.length

    for (const evt of newEvents) {
      if (evt.type === 'chat_thinking' && evt.data?.chat_id === activeChatId) {
        setStreaming(true)
        setThinkingText(prev => prev + (evt.data.text || ''))
      }
      if (evt.type === 'chat_stream' && evt.data?.chat_id === activeChatId) {
        setStreaming(true)
        setStreamingText(prev => prev + (evt.data.text || ''))
      }
      if (evt.type === 'chat_complete' && evt.data?.chat_id === activeChatId) {
        setStreaming(false)
        setStreamingText('')
        setThinkingText('')
        // Reload messages and files
        api.getChat(engagementId, activeChatId)
          .then(data => setMessages(data.messages || []))
          .catch(console.error)
        loadFiles()
      }
      if (evt.type === 'chat_error' && evt.data?.chat_id === activeChatId) {
        setStreaming(false)
        setStreamingText('')
        setError(evt.data.error || 'Chat error')
        setTimeout(() => setError(''), 5000)
      }
    }
  }, [events, activeChatId, engagementId])

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingText])

  // Focus input when chat changes
  useEffect(() => {
    if (activeChatId) inputRef.current?.focus()
  }, [activeChatId])

  async function handleNewChat() {
    try {
      const chat = await api.createChat(engagementId)
      await loadChats()
      setSearchParams({ chat: chat.id })
    } catch (e) {
      console.error('Failed to create chat:', e)
    }
  }

  async function handleDeleteChat(e, chatId) {
    e.stopPropagation()
    try {
      await api.deleteChat(engagementId, chatId)
      if (activeChatId === chatId) {
        setSearchParams({})
        setMessages([])
      }
      await loadChats()
    } catch (e) {
      console.error('Failed to delete chat:', e)
    }
  }

  async function handleSend() {
    const content = input.trim()
    if (!content || streaming || !activeChatId) return

    setInput('')
    setError('')

    // Optimistic update — show user message immediately
    const tempMsg = { id: 'temp-' + Date.now(), role: 'user', content, created_at: new Date().toISOString() }
    setMessages(prev => [...prev, tempMsg])

    try {
      await api.sendChatMessage(engagementId, activeChatId, content)
      setStreaming(true)
      // Refresh chat list to get updated title
      loadChats()
    } catch (e) {
      setError(e.message || 'Failed to send message')
      // Remove optimistic message on failure
      setMessages(prev => prev.filter(m => m.id !== tempMsg.id))
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      handleSend()
    }
  }

  function selectChat(chatId) {
    setSearchParams({ chat: chatId })
  }

  function formatTime(iso) {
    if (!iso) return ''
    const d = new Date(iso)
    const now = new Date()
    const diffMs = now - d
    const diffMin = Math.floor(diffMs / 60000)
    if (diffMin < 1) return 'just now'
    if (diffMin < 60) return `${diffMin}m ago`
    const diffH = Math.floor(diffMin / 60)
    if (diffH < 24) return `${diffH}h ago`
    return d.toLocaleDateString()
  }

  if (loading) return <div className="empty-state"><p>Loading...</p></div>

  return (
    <div style={{ padding: '20px 0' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <Link to={`/engagements/${engagementId}`} className="btn btn-secondary btn-sm">&larr; Back</Link>
        <h2 style={{ margin: 0, fontSize: 18 }}>{engagement?.name || 'Engagement'} &mdash; Chat</h2>
        <span className={`ws-status ${connected ? 'connected' : 'disconnected'}`}>
          {connected ? 'Live' : 'Disconnected'}
        </span>
        <button className={`btn btn-sm ${showFiles ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => { setShowFiles(!showFiles); if (!showFiles) loadFiles() }}>
          Files {files.length > 0 && `(${files.length})`}
        </button>
      </div>

      {error && (
        <div style={{ padding: '8px 12px', background: 'var(--color-error)', color: '#fff',
                      borderRadius: 6, marginBottom: 12, fontSize: 13 }}>
          {error}
        </div>
      )}

      <div className="chat-layout">
        {/* Sidebar */}
        <div className="chat-sidebar">
          <div className="chat-sidebar-header">
            <span style={{ fontWeight: 600, fontSize: 13 }}>Chats</span>
            <button className="btn btn-primary btn-sm" onClick={handleNewChat}>+ New</button>
          </div>
          <div className="chat-list">
            {chats.length === 0 ? (
              <div style={{ padding: 16, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
                No chats yet
              </div>
            ) : (
              chats.map(chat => (
                <div
                  key={chat.id}
                  className={`chat-list-item ${activeChatId === chat.id ? 'active' : ''}`}
                  onClick={() => selectChat(chat.id)}
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 13 }}>
                      {chat.title}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>
                      {formatTime(chat.updated_at)}
                    </div>
                  </div>
                  <button
                    className="btn-icon"
                    style={{ flexShrink: 0, width: 24, height: 24, opacity: 0.5 }}
                    onClick={(e) => handleDeleteChat(e, chat.id)}
                    title="Delete chat"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                    </svg>
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Main chat area */}
        <div className="chat-main">
          {!activeChatId ? (
            <div className="chat-empty">
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 32, marginBottom: 8, opacity: 0.3 }}>
                  <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                  </svg>
                </div>
                <p style={{ marginBottom: 12 }}>Select a chat or start a new one</p>
                <button className="btn btn-primary" onClick={handleNewChat}>New Chat</button>
              </div>
            </div>
          ) : (
            <>
              <div className="chat-messages">
                {messages.map(msg => (
                  <div key={msg.id} className={`chat-msg-row ${msg.role === 'user' ? 'chat-msg-row-right' : 'chat-msg-row-left'}`}>
                    <div className={`chat-msg-bubble ${msg.role === 'user' ? 'chat-msg-user' : 'chat-msg-assistant'}`}>
                      {msg.role === 'assistant' ? (
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                      ) : (
                        <span style={{ whiteSpace: 'pre-wrap' }}>{msg.content}</span>
                      )}
                    </div>
                  </div>
                ))}

                {/* Streaming thinking + partial message */}
                {streaming && thinkingText && !streamingText && (
                  <div className="chat-msg-row chat-msg-row-left">
                    <details className="chat-thinking-block" open>
                      <summary>Thinking...</summary>
                      <div className="chat-thinking-content">{thinkingText}</div>
                    </details>
                  </div>
                )}
                {streaming && (
                  <div className="chat-msg-row chat-msg-row-left">
                    <div className="chat-msg-bubble chat-msg-assistant chat-msg-streaming">
                      {thinkingText && streamingText && (
                        <details className="chat-thinking-block-inline">
                          <summary>Thought for a moment</summary>
                          <div className="chat-thinking-content">{thinkingText}</div>
                        </details>
                      )}
                      {streamingText ? (
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{streamingText}</ReactMarkdown>
                      ) : !thinkingText ? (
                        <span className="chat-thinking-label">Thinking...</span>
                      ) : null}
                      <span className="chat-cursor" />
                    </div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {/* Input bar */}
              <div className="chat-input-bar">
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={streaming ? 'Waiting for response...' : 'Ask about this engagement...'}
                  disabled={streaming}
                  rows={1}
                  onInput={(e) => {
                    e.target.style.height = 'auto'
                    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
                  }}
                />
                <button
                  className="btn btn-primary"
                  onClick={handleSend}
                  disabled={streaming || !input.trim()}
                  style={{ height: 40 }}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <line x1="22" y1="2" x2="11" y2="13" />
                    <polygon points="22 2 15 22 11 13 2 9 22 2" />
                  </svg>
                </button>
              </div>
            </>
          )}
        </div>

        {/* Files panel */}
        {showFiles && (
          <div className="chat-files-panel">
            <div className="chat-sidebar-header">
              <span style={{ fontWeight: 600, fontSize: 13 }}>Workspace Files</span>
              <button className="btn btn-sm btn-secondary" onClick={loadFiles}>Refresh</button>
            </div>
            <div className="chat-files-list">
              {files.length === 0 ? (
                <div style={{ padding: 16, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13 }}>
                  No files yet
                </div>
              ) : (
                files.map(f => (
                  <div key={f.path} className="chat-file-item">
                    <span className="chat-file-icon">{f.is_dir ? '📁' : '📄'}</span>
                    <span className="chat-file-name" title={f.path}>{f.name}</span>
                    {!f.is_dir && (
                      <>
                        <span className="chat-file-size">{f.size < 1024 ? `${f.size}B` : `${(f.size/1024).toFixed(1)}K`}</span>
                        <a href={api.chatFileDownloadUrl(engagementId, f.path)}
                           className="btn btn-sm btn-secondary"
                           download style={{ padding: '2px 8px', fontSize: 11 }}>
                          Download
                        </a>
                      </>
                    )}
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

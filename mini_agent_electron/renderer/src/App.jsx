import { useState, useRef, useEffect, useCallback, startTransition, useDeferredValue, useMemo } from 'react';
import useSmoothStream from './hooks/useSmoothStream';
import useTheme from './hooks/useTheme';
import LogLine from './components/LogLine';
import CodeBlock from './components/CodeBlock';
import RoundedFrame from './components/RoundedFrame';
import AgentTree from './components/AgentTree';
import CharStream from './components/CharStream';
import DeferredMarkdown from './components/DeferredMarkdown';
import StreamingMessage from './components/StreamingMessage';
import ErrorBoundary from './components/ErrorBoundary';
import SettingsPanel from './components/SettingsPanel';
import ToolCard from './components/ToolCard';
import Header from './components/Header';
import StatusBar from './components/StatusBar';

// Cap rendered DOM nodes to prevent lag at long conversations (300+ turns).
// State arrays still hold full history; only the visible slice hits the DOM.
const MAX_RENDERED_CHAT_LINES = 400;
const MAX_RENDERED_TOOL_LINES = 400;


// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
function AppShell() {
  // Log state -- arrays of { text, cls?, html?, icon? }
  const [toolsLines, setToolsLines] = useState([]);
  const [chatLines, setChatLines] = useState([]);

  // Tool Cards state -- Dirac-inspired card-based display
  // Each card: { id, toolName, toolArgs, status, output, startTime, endTime, diffPreview, errorDetail }
  const [toolCards, setToolCards] = useState([]);
  const toolCardIdRef = useRef(0);

  // Deferred values keep the UI responsive during heavy streaming
  const deferredToolsLines = useDeferredValue(toolsLines);
  const deferredChatLines = useDeferredValue(chatLines);

  // Sub-agent data -- { [task_id]: { name, desc, toolCalls: [], thoughts: [], output: "", ok: null } }
  const [subagentData, setSubagentData] = useState({});
  const deferredSubagentData = useDeferredValue(subagentData);

  // Smooth streaming for thinking & chat
  const thinking = useSmoothStream();
  const chatStream = useSmoothStream();

  // UI state
  const [modelName, setModelName] = useState('starting...');
  const [sessionName, setSessionName] = useState('');
  const [gitBranch, setGitBranch] = useState('');
  const [gitDirty, setGitDirty] = useState(false);
  const [workspace, setWorkspace] = useState('');
  const [restoredCount, setRestoredCount] = useState(null);
  const [isLive, setIsLive] = useState(false);
  const [turnCountVal, setTurnCountVal] = useState(null);
  const [elapsedSec, setElapsedSec] = useState(null);
  const [inputDisabled, setInputDisabled] = useState(false);
  const [thinkingBlocks, setThinkingBlocks] = useState([]);
  const deferredThinkingBlocks = useDeferredValue(thinkingBlocks);
  const [botStatus, setBotStatus] = useState({});
  const [provider, setProvider] = useState('deepseek');

  // Reasonix-style status bar state
  const [balanceDisplay, setBalanceDisplay] = useState(null);
  const [sessionCost, setSessionCost] = useState('-');
  const [turnCost, setTurnCost] = useState('-');
  const [cacheHitRate, setCacheHitRate] = useState(null);
  const [subagentRunning, setSubagentRunning] = useState(0);

  // Theme hook (extracted)
  const {
    theme, themeEntry, PALETTE_SVG, THEMES,
    themePickerOpen, setThemePickerOpen, themeToggleRef, dropdownPos,
    applyTheme, cycleTheme,
  } = useTheme();

  const inputRef = useRef(null);
  const thinkingLogRef = useRef(null);
  const chatLogRef = useRef(null);
  const toolsLogRef = useRef(null);
  const inThinkingRef = useRef(false);
  const submitTimeoutRef = useRef(null);
  const timerRef = useRef(null);
  const turnStartRef = useRef(null);
  const toolOutputStack = useRef([]); // stack of buffers for parallel tool calls
  const lineIdRef = useRef(0); // monotonically increasing ID for stable React keys
  const nextLineId = useCallback(() => ++lineIdRef.current, []);

  const startTimer = useCallback(() => {
    if (timerRef.current) return; // already running
    turnStartRef.current = Date.now();
    setElapsedSec(0);
    timerRef.current = setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - turnStartRef.current) / 1000));
    }, 1000);
  }, []);

  const stopTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (turnStartRef.current) {
      setElapsedSec(Math.floor((Date.now() - turnStartRef.current) / 1000));
      turnStartRef.current = null;
    }
  }, []);
  const [showSettings, setShowSettings] = useState(false);
  const [inputValue, setInputValue] = useState('');

  // Helper to add a line to any log -- uses startTransition for non-blocking UI
  const addLine = useCallback((setter) => (line) => {
    startTransition(() => {
      setter((prev) => [...prev, line]);
    });
  }, []);

  const addToolLine = useCallback((line) => addLine(setToolsLines)(line), [addLine]);

  // Status / init -- fetched once on mount (empty deps to avoid re-render loop)
  useEffect(() => {
    const api = window.miniAgent;
    if (!api) return;

    const onStatus = (data) => {
      // Check for no-API-key signal from main process
      if (data.reason === 'no_api_key') {
        setShowSettings(true);
        return;
      }
      if (data.ready) {
        // Backend came online -- hide settings if it was showing
        setShowSettings(false);
      }
      if (data.model != null) setModelName(data.model);
      if (data.provider != null) setProvider(data.provider);
      if (data.session_name != null) setSessionName(data.session_name);
      if (data.workspace != null) setWorkspace(data.workspace);
      if (data.git_branch != null) {
        setGitBranch(data.git_branch);
        setGitDirty(!!data.git_dirty);
      }
      if (data.restored_count != null) setRestoredCount(data.restored_count);
      // Reasonix-style status bar fields
      if (data.balance != null) setBalanceDisplay(data.balance);
      if (data.session_cost != null) setSessionCost(data.session_cost);
      if (data.turn_cost != null) setTurnCost(data.turn_cost);
      if (data.cache_hit_rate != null) setCacheHitRate(data.cache_hit_rate);
      if (data.subagent_running != null) setSubagentRunning(data.subagent_running);
      if (data.ready) {
        addToolLine({ text: 'backend ready', cls: 'dim' });
      }
    };
    const unsub = api.on('backend:status', onStatus);

    // Fetch cached status from main process (handles race where backend
    // sent status before our listener was registered)
    api.getStatus?.().then((data) => {
      if (!data) return;
      onStatus(data);
    });

    return () => unsub();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Discord bot status listener
  useEffect(() => {
    const api = window.miniAgent;
    if (!api) return;
    const unsub = api.on('backend:bot_status', (data) => {
      setBotStatus((prev) => ({ ...prev, [data.name]: data.alive }));
    });
    return () => unsub();
  }, []);

  // Stream listeners
  useEffect(() => {
    const api = window.miniAgent;
    if (!api) return;

    const unsubs = [];

    unsubs.push(api.on('stream:token', (data) => {
      if (inThinkingRef.current) {
        thinking.addChunk(data.text);
      } else {
        chatStream.addChunk(data.text);
      }
    }));

    unsubs.push(api.on('stream:thinking_start', () => {
      inThinkingRef.current = true;
      thinking.reset();
    }));

    unsubs.push(api.on('stream:thinking_end', () => {
      inThinkingRef.current = false;
      const flushed = thinking.flush();
      if (flushed) startTransition(() => setThinkingBlocks((prev) => [...prev, flushed]));
    }));

    unsubs.push(api.on('stream:tool_start', (data) => {
      const summary = data.summary || data.tool_name || '?';
      const parenIdx = summary.indexOf('(');
      let toolName, toolArgs;
      if (parenIdx > 0) {
        toolName = summary.slice(0, parenIdx);
        toolArgs = summary.slice(parenIdx);
      } else {
        toolName = summary;
        toolArgs = '';
      }

      // Also keep the flat log line for backward compat
      addToolLine({ toolName, toolArgs, cls: 'tool-summary' });

      // Create a card
      const cardId = ++toolCardIdRef.current;
      startTransition(() => {
        setToolCards((prev) => [...prev, {
          id: cardId,
          toolName,
          toolArgs,
          status: 'running',
          output: '',
          startTime: Date.now(),
          endTime: null,
          diffPreview: null,
          errorDetail: null,
        }]);
      });

      // Push a buffer for tool output accumulation
      toolOutputStack.current.push({ cardId, buffer: '' });
    }));

    unsubs.push(api.on('stream:tool_chunk', (data) => {
      const stack = toolOutputStack.current;
      if (stack.length === 0) return;
      const top = stack[stack.length - 1];
      top.buffer += data.text || '';

      startTransition(() => {
        setToolCards((prev) =>
          prev.map((c) =>
            c.id === top.cardId ? { ...c, output: top.buffer } : c
          )
        );
      });
    }));

    unsubs.push(api.on('stream:tool_end', (data) => {
      const stack = toolOutputStack.current;
      // Pop matching buffer
      let cardId = null;
      let finalBuffer = '';
      while (stack.length > 0) {
        const top = stack.pop();
        finalBuffer = top.buffer;
        cardId = top.cardId;
        break;
      }

      const now = Date.now();
      const status = data.ok ? 'ok' : 'error';
      if (cardId != null) {
        const code = finalBuffer || data.content || '';
        const diffPreview = data.diff_preview || null;
        const errorDetail = !data.ok ? (data.detail || '') : '';
        startTransition(() => {
          setToolCards((prev) =>
            prev.map((c) =>
              c.id === cardId
                ? { ...c, status, endTime: now, output: code, diffPreview, errorDetail }
                : c
            )
          );
        });
      }
    }));

    unsubs.push(api.on('stream:turn_complete', (data) => {
      clearTimeout(submitTimeoutRef.current);
      const agentText = chatStream.flush();
      // Always resolve the pending placeholder, even when the model
      // produced no text content (e.g. reasoning-only + tool_calls).
      // Otherwise the placeholder stays as an empty line in chat forever,
      // and the user sees no output summary.
      startTransition(() => {
        setChatLines((prev) => {
          const updated = [...prev];
          if (updated.length > 0 && updated[updated.length - 1].cls === 'msg-agent-pending') {
            updated[updated.length - 1] = { id: updated[updated.length - 1].id, text: agentText || '', cls: 'msg-agent', markdown: true };
          } else if (agentText) {
            updated.push({ id: nextLineId(), text: agentText, cls: 'msg-agent', markdown: true });
          }
          return updated;
        });
      });
      chatStream.reset();
      if (data.turn_count) setTurnCountVal(data.turn_count);
      // Reasonix-style cost updates from turn_complete
      if (data.usage?.turn_cost) setTurnCost(data.usage.turn_cost);
      if (data.usage?.session_cost) setSessionCost(data.usage.session_cost);
      if (data.usage?.cache_hit_rate != null) setCacheHitRate(data.usage.cache_hit_rate);
      if (data.usage?.subagent_running != null) setSubagentRunning(data.usage.subagent_running);
      // Balance -- pushed on every turn_complete so the wallet display updates live
      if (data.usage?.balance != null) setBalanceDisplay(data.usage.balance);
      // NOTE: Do NOT set isLive=false here.  The agent may start another
      // turn immediately (sub-agent auto-report, tool continuations, etc.).
      // Only the 'idle' message (sent when _turn_loop truly drains the
      // queue) should reset isLive.
    }));

    unsubs.push(api.on('stream:error', (data) => {
      clearTimeout(submitTimeoutRef.current);
      stopTimer();
      const agentText = chatStream.flush();
      chatStream.reset();
      startTransition(() => {
        setChatLines((prev) => {
          const updated = [...prev];
          // Flush any pending agent text before showing the error
          if (agentText && updated.length > 0 && updated[updated.length - 1].cls === 'msg-agent-pending') {
            updated[updated.length - 1] = { id: updated[updated.length - 1].id, text: agentText, cls: 'msg-agent', markdown: true };
          }
          updated.push({ id: nextLineId(), text: `Error: ${data.message}`, cls: 'msg-error' });
          return updated;
        });
      });
      setIsLive(false);
      setInputDisabled(false);
      inputRef.current?.focus();
    }));

    unsubs.push(api.on('stream:status', (data) => {
      startTransition(() => {
        setChatLines((prev) => [...prev, { id: nextLineId(), text: data.message, cls: 'msg-status' }]);
      });
    }));

    unsubs.push(api.on('backend:response', (data) => {
      if (data.lines) {
        startTransition(() => {
          setChatLines((prev) => {
            const updated = [...prev];
            for (const line of data.lines) {
              updated.push({ id: nextLineId(), text: line, cls: 'msg-status' });
            }
            return updated;
          });
        });
      }
    }));

    // --- Turn lifecycle: start / idle ---
    // The backend sends turn_start at the beginning of each turn and idle
    // when the sequential turn-loop truly exits (input queue drained).
    // These provide a reliable running/cancel indicator that doesn't flicker
    // between turns.
    unsubs.push(api.on('backend:turn_start', () => {
      setIsLive(true);
      setInputDisabled(true);
      startTimer();
    }));

    unsubs.push(api.on('backend:idle', () => {
      clearTimeout(submitTimeoutRef.current);
      stopTimer();
      // Safety: flush any remaining streaming text and resolve the pending
      // placeholder.  If turn_complete was skipped or chatStream.flush()
      // returned empty, the placeholder would otherwise stay as an empty
      // line in the chat log.
      const leftover = chatStream.flush();
      if (leftover || chatStream.displayedText) {
        startTransition(() => {
          setChatLines((prev) => {
            const updated = [...prev];
            if (updated.length > 0 && updated[updated.length - 1].cls === 'msg-agent-pending') {
              updated[updated.length - 1] = { id: updated[updated.length - 1].id, text: leftover || chatStream.displayedText, cls: 'msg-agent', markdown: true };
            }
            return updated;
          });
        });
      }
      chatStream.reset();
      setIsLive(false);
      setInputDisabled(false);
      inputRef.current?.focus();
    }));

    // --- Sub-agent events ---
    unsubs.push(api.on('stream:subagent_start', (data) => {
      setSubagentRunning((c) => c + 1);
      setSubagentData((prev) => ({
        ...prev,
        [data.task_id]: {
          name: data.name,
          desc: data.desc,
          parent_id: data.parent_id || 'orchestrator',
          toolCalls: [],
          thoughts: [],
          output: '',
          ok: null,
        },
      }));
    }));

    unsubs.push(api.on('stream:subagent_tool_start', (data) => {
      setSubagentData((prev) => {
        const agent = prev[data.task_id];
        if (!agent) return prev;
        return {
          ...prev,
          [data.task_id]: {
            ...agent,
            toolCalls: [...agent.toolCalls, {
              toolName: data.tool_name,
              toolArgs: data.tool_args ? `(${data.tool_args.slice(0, 80)})` : '',
              ok: null,
            }],
          },
        };
      });
    }));

    unsubs.push(api.on('stream:subagent_tool_end', (data) => {
      setSubagentData((prev) => {
        const agent = prev[data.task_id];
        if (!agent) return prev;
        const toolCalls = [...agent.toolCalls];
        // Mark the last matching tool call as complete
        for (let i = toolCalls.length - 1; i >= 0; i--) {
          if (toolCalls[i].toolName === data.tool_name && toolCalls[i].ok === null) {
            toolCalls[i] = { ...toolCalls[i], ok: data.ok, result: data.content?.slice(0, 200) };
            break;
          }
        }
        return { ...prev, [data.task_id]: { ...agent, toolCalls } };
      });
    }));

    unsubs.push(api.on('stream:subagent_thought', (data) => {
      setSubagentData((prev) => {
        const agent = prev[data.task_id];
        if (!agent) return prev;
        // Keep last 30 thought chunks to avoid unbounded growth
        const thoughts = [...agent.thoughts, data.text].slice(-30);
        return { ...prev, [data.task_id]: { ...agent, thoughts } };
      });
    }));

    unsubs.push(api.on('stream:subagent_output', (data) => {
      // subagent_output is still sent for backward compat; accumulate into thoughts
      setSubagentData((prev) => {
        const agent = prev[data.task_id];
        if (!agent) return prev;
        const thoughts = [...agent.thoughts, data.line].slice(-30);
        return { ...prev, [data.task_id]: { ...agent, thoughts } };
      });
    }));

    unsubs.push(api.on('stream:subagent_end', (data) => {
      setSubagentRunning((c) => Math.max(0, c - 1));
      setSubagentData((prev) => {
        const agent = prev[data.task_id];
        if (!agent) return prev;
        return {
          ...prev,
          [data.task_id]: {
            ...agent,
            ok: data.ok,
            output: data.output || agent.output,
          },
        };
      });
    }));

    return () => unsubs.forEach((u) => u());
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Submit handler
  const handleSubmit = useCallback((text) => {
    if (inputDisabled || !text?.trim()) return;

    // Add user message
    startTransition(() => {
      setChatLines((prev) => [
        ...prev,
        { id: nextLineId(), text: text.trim(), cls: 'msg-user' },
        { id: nextLineId(), text: '', cls: 'msg-agent-pending' },
      ]);
    });
    chatStream.reset();

    setIsLive(true);
    setInputDisabled(true);
    setInputValue('');

    window.miniAgent.submit(text);

    // Safety timeout -- re-enable input after 120s in case the backend
    // hangs or crashes.  The idle message handles normal completion;
    // this is a last-resort fallback.
    submitTimeoutRef.current = setTimeout(() => {
      setInputDisabled(false);
      inputRef.current?.focus();
    }, 120_000);
  }, [inputDisabled, chatStream]);

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e.target.value);
    }
  }, [handleSubmit]);

  const handleChange = useCallback((e) => {
    setInputValue(e.target.value);
  }, []);

  // Drag-and-drop: use the preload bridge which can read Electron's File.path.
  // The preload manages dragOver/drop at the document level and calls our
  // callback with absolute file paths.
  useEffect(() => {
    const api = window.miniAgent;
    if (!api || !api.onFileDrop) return;
    const unsub = api.onFileDrop((paths) => {
      setInputValue((prev) => {
        const appended = paths.join(' ');
        return prev ? `${prev} ${appended}` : appended;
      });
      inputRef.current?.focus();
    });
    return () => unsub();
  }, []);

  // Click workspace to change it
  const handleWorkspaceClick = useCallback(async () => {
    const api = window.miniAgent;
    if (!api) return;
    const newPath = await api.openWorkspace();
    if (newPath) {
      api.saveWorkspace(newPath);
      handleSubmit(`/workspace ${newPath}`);
    }
  }, [handleSubmit]);

  // Session picker handler
  const handleSessionSwitch = useCallback((name, isNew) => {
    const api = window.miniAgent;
    if (!api) return;
    if (isNew) {
      api.newSession(name);
    } else {
      api.switchSession(name);
    }
    // Session name in footer will update via backend:status event
  }, []);

  // Settings saved handler -- backend will send backend:status { ready: true }
  // which triggers setShowSettings(false) in the onStatus listener
  const handleSettingsSaved = useCallback(() => {
    // Let the backend:status event handle hiding the panel
  }, []);

  // Cancel handler -- immediately reset UI, then tell backend
  const handleCancel = useCallback(() => {
    window.miniAgent?.cancel();
    clearTimeout(submitTimeoutRef.current);
    stopTimer();
    inThinkingRef.current = false;
    const agentText = chatStream.flush();
    const thinkText = thinking.flush();
    if (thinkText) startTransition(() => setThinkingBlocks((prev) => [...prev, thinkText]));
    thinking.reset();
    if (agentText) {
      startTransition(() => {
        setChatLines((prev) => {
          const updated = [...prev];
          if (updated.length > 0 && updated[updated.length - 1].cls === 'msg-agent-pending') {
            updated[updated.length - 1] = { id: updated[updated.length - 1].id, text: agentText, cls: 'msg-agent' };
          }
          return updated;
        });
      });
      chatStream.reset();
    }
    setIsLive(false);
    setInputDisabled(false);
    setInputValue('');
    inputRef.current?.focus();
  }, [chatStream, thinking, stopTimer]);

  // Auto-scroll thinking log
  useEffect(() => {
    const el = thinkingLogRef.current;
    if (el) {
      requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
    }
  }, [thinking.displayedText]);

  // Auto-scroll chat log — fire on both the immediate and deferred values
  // so we catch the DOM paint. requestAnimationFrame ensures scrollHeight is
  // measured after React has committed the new nodes.
  useEffect(() => {
    const el = chatLogRef.current;
    if (el) {
      requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
    }
  }, [chatLines, deferredChatLines, chatStream.displayedText]);

  // Auto-scroll tools log (cards + lines)
  useEffect(() => {
    const el = toolsLogRef.current;
    if (el) {
      requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
    }
  }, [toolCards, deferredToolsLines]);

  // Auto-focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      clearTimeout(submitTimeoutRef.current);
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    };
  }, []);

  // Memoized visible slices to cap rendered DOM nodes
  const visibleToolLines = useMemo(() => {
    if (deferredToolsLines.length <= MAX_RENDERED_TOOL_LINES) return deferredToolsLines;
    return deferredToolsLines.slice(-MAX_RENDERED_TOOL_LINES);
  }, [deferredToolsLines]);

  const visibleChatLines = useMemo(() => {
    if (deferredChatLines.length <= MAX_RENDERED_CHAT_LINES) return deferredChatLines;
    return deferredChatLines.slice(-MAX_RENDERED_CHAT_LINES);
  }, [deferredChatLines]);

  return (
    <div id="app">
      {/* Header */}
      <Header modelName={modelName} cacheHitRate={cacheHitRate} subagentRunning={subagentRunning} />

      {/* Thinking panel */}
      <RoundedFrame id="thinking-frame" title="Thinking">
        <div ref={thinkingLogRef} className="thinking-log">
          <div className="frame-content">
            {deferredThinkingBlocks.map((block, i) => (
              <CharStream key={i} text={block} />
            ))}
            {thinking.displayedText && <CharStream text={thinking.displayedText} />}
          </div>
        </div>
      </RoundedFrame>

      {/* Main row: Tools + Chat + Sub-agents */}
      <div id="main-row">
        <RoundedFrame id="tools-frame" title="Tools">
          <div ref={toolsLogRef} className="tools-log">
            <div className="frame-content">
              {toolCards.map((card) => (
                <ToolCard key={card.id} card={card} />
              ))}
              {visibleToolLines.map((line, i) => (
                <LogLine key={i} line={line} />
              ))}
            </div>
          </div>
        </RoundedFrame>

        <RoundedFrame id="chat-frame" title="Chat">
          <div ref={chatLogRef} className="chat-log">
            <div className="frame-content">
              {visibleChatLines.map((line) => {
                if (line.markdown) {
                  return <DeferredMarkdown key={line.id} text={line.text} cls={line.cls} />;
                }
                return <LogLine key={line.id} line={line} />;
              })}
              {chatStream.displayedText && (
                <div className="msg-agent">
                  <StreamingMessage text={chatStream.displayedText} />
                </div>
              )}
            </div>
          </div>
        </RoundedFrame>

        {/* Sub-agents pane */}
        {Object.keys(deferredSubagentData).length > 0 && (
          <RoundedFrame id="subagents-frame" title="Sub-agents">
            <AgentTree data={deferredSubagentData} />
          </RoundedFrame>
        )}
      </div>

      {/* Input */}
      <div id="input-frame" className={`rounded-frame${isLive ? ' live' : ''}`}>
        <div className="frame-body">
          <div className="frame-content">
            <div id="input-container">
              <span className="prompt">{'>'}</span>
              <input
                ref={inputRef}
                type="text"
                id="user-input"
                placeholder="Type a message, /command, or drop files here..."
                autoFocus
                autoComplete="off"
                spellCheck="false"
                value={inputValue}
                onChange={handleChange}
                onKeyDown={handleKeyDown}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Status bar */}
      <StatusBar
        balanceDisplay={balanceDisplay}
        gitBranch={gitBranch}
        gitDirty={gitDirty}
        botStatus={botStatus}
        setBotStatus={setBotStatus}
        isLive={isLive}
        elapsedSec={elapsedSec}
        turnCountVal={turnCountVal}
        turnCost={turnCost}
        subagentRunning={subagentRunning}
        restoredCount={restoredCount}
        workspace={workspace}
        sessionName={sessionName}
        themeEntry={themeEntry}
        PALETTE_SVG={PALETTE_SVG}
        THEMES={THEMES}
        theme={theme}
        themePickerOpen={themePickerOpen}
        setThemePickerOpen={setThemePickerOpen}
        themeToggleRef={themeToggleRef}
        dropdownPos={dropdownPos}
        applyTheme={applyTheme}
        handleCancel={handleCancel}
        handleWorkspaceClick={handleWorkspaceClick}
        handleSessionSwitch={handleSessionSwitch}
      />
      {showSettings && <SettingsPanel onSaved={handleSettingsSaved} />}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Root export -- wraps App in Error Boundary
// ---------------------------------------------------------------------------
export default function App() {
  return (
    <ErrorBoundary>
      <AppShell />
    </ErrorBoundary>
  );
}

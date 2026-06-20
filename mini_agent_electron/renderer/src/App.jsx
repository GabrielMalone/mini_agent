import { useState, useRef, useEffect, useCallback, startTransition, useDeferredValue } from 'react';
import useSmoothStream from './hooks/useSmoothStream';
import useTheme from './hooks/useTheme';
import RoundedFrame from './components/RoundedFrame';
import AgentTree from './components/AgentTree';
import CharStream from './components/CharStream';
import TerminalBlock from './components/TerminalBlock';
import ErrorBoundary from './components/ErrorBoundary';
import SettingsPanel from './components/SettingsPanel';
import ToolCard from './components/ToolCard';
import Header from './components/Header';
import StatusBar from './components/StatusBar';
import TerminalPanel from './components/TerminalPanel';


// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
function AppShell() {
  // Terminal blocks -- Warp-style command+output blocks
  // Each block: { id, command, output, status, timestamp }
  // status: 'running' | 'ok' | 'err'
  const [blocks, setBlocks] = useState([]);

  // Command history for Up/Down navigation in ShellInput
  const [commandHistory, setCommandHistory] = useState([]);
  // User commands shown in the terminal panel history (scrollable)
  const [userCommands, setUserCommands] = useState([]);
  // Shell command output (from /sh) displayed in the terminal panel
  const [shellOutput, setShellOutput] = useState([]);
  const activeBlockIdRef = useRef(null);  // ID of the currently streaming block

  // Tool Cards state -- Dirac-inspired card-based display
  // Each card: { id, toolName, toolArgs, status, output, startTime, endTime, diffPreview, errorDetail }
  const [toolCards, setToolCards] = useState([]);
  const toolCardIdRef = useRef(0);

  // Deferred values keep the UI responsive during heavy streaming
  const deferredBlocks = useDeferredValue(blocks);

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
  const [planSteps, setPlanSteps] = useState([]);
  const [planDone, setPlanDone] = useState([]);

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

  // Status / init -- fetched once on mount (empty deps to avoid re-render loop)
  useEffect(() => {
    const api = window.miniAgent;
    if (!api) return;

    const onStatus = (data) => {
      if (data.reason === 'no_api_key') {
        setShowSettings(true);
        return;
      }
      if (data.ready) setShowSettings(false);
      if (data.model != null) setModelName(data.model);
      if (data.provider != null) setProvider(data.provider);
      if (data.session_name != null) setSessionName(data.session_name);
      if (data.workspace != null) setWorkspace(data.workspace);
      if (data.git_branch != null) {
        setGitBranch(data.git_branch);
        setGitDirty(!!data.git_dirty);
      }
      if (data.restored_count != null) setRestoredCount(data.restored_count);
      if (data.balance != null) setBalanceDisplay(data.balance);
      if (data.session_cost != null) setSessionCost(data.session_cost);
      if (data.turn_cost != null) setTurnCost(data.turn_cost);
      if (data.cache_hit_rate != null) setCacheHitRate(data.cache_hit_rate);
      if (data.subagent_running != null) setSubagentRunning(data.subagent_running);
      if (data.plan_steps != null) setPlanSteps(data.plan_steps);
      if (data.plan_done != null) setPlanDone(data.plan_done);
    };
    const unsub = api.on('backend:status', onStatus);
    api.getStatus?.().then((data) => { if (data) onStatus(data); });
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
      const cardId = ++toolCardIdRef.current;
      startTransition(() => {
        setToolCards((prev) => [...prev, {
          id: cardId, toolName, toolArgs, status: 'running', output: '',
          startTime: Date.now(), endTime: null, diffPreview: null, errorDetail: null,
        }]);
      });
      toolOutputStack.current.push({ cardId, buffer: '' });
    }));

    unsubs.push(api.on('stream:tool_output', (data) => {
      const stack = toolOutputStack.current;
      if (stack.length === 0) return;
      const top = stack[stack.length - 1];
      top.buffer += data.line || '';
      startTransition(() => {
        setToolCards((prev) =>
          prev.map((c) => c.id === top.cardId ? { ...c, output: top.buffer } : c)
        );
      });
    }));

    unsubs.push(api.on('stream:tool_end', (data) => {
      const stack = toolOutputStack.current;
      let cardId = null;
      let finalBuffer = '';
      while (stack.length > 0) {
        const top = stack.pop();
        finalBuffer = top.buffer;
        cardId = top.cardId;
        break;
      }
      const now = Date.now();
      const status = data.ok ? 'ok' : 'err';
      if (cardId != null) {
        const code = finalBuffer || data.content || '';
        const diffPreview = data.diff_preview || null;
        const errorDetail = !data.ok ? (data.detail || '') : '';
        startTransition(() => {
          setToolCards((prev) =>
            prev.map((c) =>
              c.id === cardId ? { ...c, status, endTime: now, output: code, diffPreview, errorDetail } : c
            )
          );
        });
      }
    }));

    unsubs.push(api.on('stream:turn_complete', (data) => {
      clearTimeout(submitTimeoutRef.current);
      const agentText = chatStream.flush();
      const activeId = activeBlockIdRef.current;
      startTransition(() => {
        setBlocks((prev) =>
          prev.map((b) =>
            b.id === activeId
              ? { ...b, output: agentText || b.output, status: 'ok' }
              : b
          )
        );
      });
      activeBlockIdRef.current = null;
      chatStream.reset();
      if (data.turn_count) setTurnCountVal(data.turn_count);
      if (data.usage?.turn_cost) setTurnCost(data.usage.turn_cost);
      if (data.usage?.session_cost) setSessionCost(data.usage.session_cost);
      if (data.usage?.cache_hit_rate != null) setCacheHitRate(data.usage.cache_hit_rate);
      if (data.usage?.subagent_running != null) setSubagentRunning(data.usage.subagent_running);
      if (data.usage?.balance != null) setBalanceDisplay(data.usage.balance);
      if (data.plan_steps != null) setPlanSteps(data.plan_steps);
      if (data.plan_done != null) setPlanDone(data.plan_done);
    }));

    unsubs.push(api.on('stream:error', (data) => {
      clearTimeout(submitTimeoutRef.current);
      stopTimer();
      const agentText = chatStream.flush();
      const activeId = activeBlockIdRef.current;
      chatStream.reset();
      startTransition(() => {
        setBlocks((prev) =>
          prev.map((b) =>
            b.id === activeId
              ? { ...b, output: agentText ? `${agentText}\n\nError: ${data.message}` : `Error: ${data.message}`, status: 'err' }
              : b
          )
        );
      });
      activeBlockIdRef.current = null;
      setIsLive(false);
      setInputDisabled(false);
      inputRef.current?.focus();
    }));

    unsubs.push(api.on('stream:status', (data) => {
      startTransition(() => {
        setBlocks((prev) => [...prev, {
          id: nextLineId(), command: '', output: data.message, status: 'ok', timestamp: Date.now(),
        }]);
      });
    }));

    unsubs.push(api.on('backend:response', (data) => {
      if (data.lines) {
        const activeId = activeBlockIdRef.current;
        const output = data.lines.join('\n');
        const blockStatus = (data.exit_code !== undefined && data.exit_code !== 0) ? 'err' : 'ok';
        startTransition(() => {
          if (activeId) {
            // Update the existing running block created by handleSubmit
            setBlocks((prev) =>
              prev.map((b) =>
                b.id === activeId
                  ? { ...b, output, status: blockStatus }
                  : b
              )
            );
            activeBlockIdRef.current = null;
          } else {
            // Fallback: create a new block (shouldn't normally happen)
            setBlocks((prev) => [...prev, {
              id: nextLineId(), command: data.command || '', output, status: blockStatus, timestamp: Date.now(),
            }]);
          }
        });
      }
    }));

    // Shell command output (from /sh) — update the active chat block
    unsubs.push(api.on('stream:shell_output', (data) => {
      const outputText = (data.lines || []).join('\n');
      const isOk = data.exit_code === 0;
      const activeId = activeBlockIdRef.current;
      startTransition(() => {
        setBlocks((prev) => prev.map((b) => {
          if (b.id === activeId && b.status === 'running') {
            return { ...b, output: outputText, status: isOk ? 'ok' : 'err' };
          }
          return b;
        }));
      });
      // /sh output goes to chat area only; no terminal-panel duplication
    }));

    unsubs.push(api.on('backend:turn_start', () => {
      setIsLive(true);
      setInputDisabled(true);
      startTimer();
    }));

    unsubs.push(api.on('backend:idle', () => {
      clearTimeout(submitTimeoutRef.current);
      stopTimer();
      const leftover = chatStream.flush();
      const activeId = activeBlockIdRef.current;
      if (leftover || chatStream.displayedText) {
        startTransition(() => {
          setBlocks((prev) =>
            prev.map((b) =>
              b.id === activeId
                ? { ...b, output: leftover || chatStream.displayedText || b.output, status: 'ok' }
                : b
            )
          );
        });
      }
      activeBlockIdRef.current = null;
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
          name: data.name, desc: data.desc, parent_id: data.parent_id || 'orchestrator',
          toolCalls: [], thoughts: [], output: '', ok: null,
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
        const thoughts = [...agent.thoughts, data.text].slice(-30);
        return { ...prev, [data.task_id]: { ...agent, thoughts } };
      });
    }));

    unsubs.push(api.on('stream:subagent_output', (data) => {
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
        return { ...prev, [data.task_id]: { ...agent, ok: data.ok, output: data.content || agent.output } };
      });
    }));

    return () => unsubs.forEach((u) => u());
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Submit handler -- creates terminal blocks, supports slash commands.
  // Regular text: if a turn is running, queue as an interjection;
  // otherwise start a new turn with a 'running' block.
  const handleSubmit = useCallback((text) => {
    if (!text?.trim()) return;

    const trimmed = text.trim();

    // Add to command history (deduplicate consecutive identical commands)
    setCommandHistory((prev) => {
      if (prev.length > 0 && prev[prev.length - 1] === trimmed) return prev;
      return [...prev.slice(-99), trimmed];  // keep last 100
    });

    // Add to terminal panel history (visible in the expandable input area)
    setUserCommands((prev) => [
      ...prev.slice(-199),  // keep last 200
      { id: `uc-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, text: trimmed, timestamp: Date.now() },
    ]);

    // Slash commands always go through the command handler
    if (trimmed.startsWith('/')) {
      setInputValue('');
      // All slash commands create a block in the chat area (including /sh)
      const cmdId = nextLineId();
      startTransition(() => {
        setBlocks((prev) => [...prev, {
          id: cmdId, command: trimmed, output: '', status: 'running', timestamp: Date.now(),
        }]);
      });
      activeBlockIdRef.current = cmdId;
      window.miniAgent.command(trimmed);
      return;
    }

    // Regular message -- create a block
    const blockId = nextLineId();
    const blockStatus = isLive ? 'ok' : 'running';  // interjections are info blocks
    startTransition(() => {
      setBlocks((prev) => [...prev, {
        id: blockId, command: trimmed,
        output: isLive ? '(queued)' : '',
        status: blockStatus, timestamp: Date.now(),
      }]);
    });
    setInputValue('');

    if (isLive) {
      // Agent is running -- queue as interjection, don't set activeBlockId
      window.miniAgent.interject(trimmed);
    } else {
      // Agent is idle -- start a new turn
      activeBlockIdRef.current = blockId;
      chatStream.reset();
      setIsLive(true);
      setInputDisabled(true);
      window.miniAgent.submit(trimmed);

      submitTimeoutRef.current = setTimeout(() => {
        setInputDisabled(false);
        inputRef.current?.focus();
      }, 120_000);
    }
  }, [isLive, chatStream]);

  // Drag-and-drop
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
    if (isNew) api.newSession(name);
    else api.switchSession(name);
  }, []);

  // Settings saved handler
  const handleSettingsSaved = useCallback(() => {}, []);

  // Cancel handler
  const handleCancel = useCallback(() => {
    window.miniAgent?.cancel();
    clearTimeout(submitTimeoutRef.current);
    stopTimer();
    inThinkingRef.current = false;
    const agentText = chatStream.flush();
    const thinkText = thinking.flush();
    if (thinkText) startTransition(() => setThinkingBlocks((prev) => [...prev, thinkText]));
    thinking.reset();
    const activeId = activeBlockIdRef.current;
    if (agentText) {
      startTransition(() => {
        setBlocks((prev) =>
          prev.map((b) =>
            b.id === activeId
              ? { ...b, output: agentText, status: 'ok' }
              : b
          )
        );
      });
      chatStream.reset();
    }
    activeBlockIdRef.current = null;
    setIsLive(false);
    setInputDisabled(false);
    setInputValue('');
    inputRef.current?.focus();
  }, [chatStream, thinking, stopTimer]);

  // Auto-scroll thinking log
  useEffect(() => {
    const el = thinkingLogRef.current;
    if (el) requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
  }, [thinking.displayedText]);

  // Auto-scroll chat log
  useEffect(() => {
    const el = chatLogRef.current;
    if (el) requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
  }, [blocks, deferredBlocks, chatStream.displayedText]);

  // Auto-scroll tools log
  useEffect(() => {
    const el = toolsLogRef.current;
    if (el) requestAnimationFrame(() => { el.scrollTop = el.scrollHeight; });
  }, [toolCards]);

  // Auto-focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      clearTimeout(submitTimeoutRef.current);
      if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; }
    };
  }, []);

  return (
    <div id="app">
      {/* Header */}
      <Header modelName={modelName} loading={modelName === 'starting...'} cacheHitRate={cacheHitRate} subagentRunning={subagentRunning} />

      {/* Main row: Tools + Chat + Sub-agents */}
      <div id="main-row">
        {/* Left column: Tools (top) + Thinking (bottom) */}
        <div id="left-column">
          <RoundedFrame id="tools-frame" title="Tools">
            <div ref={toolsLogRef} className="tools-log">
              <div className="frame-content">
                {toolCards.map((card) => (
                  <ToolCard key={card.id} tool={card} theme={theme} />
                ))}
              </div>
            </div>
          </RoundedFrame>

          <RoundedFrame id="thinking-frame" title="Thinking">
            <div ref={thinkingLogRef} className="thinking-log">
              <div className="frame-content">
                {deferredThinkingBlocks.map((block, i) => (
                  <CharStream key={i} text={block} className="thinking" />
                ))}
                {thinking.displayedText && <CharStream text={thinking.displayedText} className="thinking" />}
              </div>
            </div>
          </RoundedFrame>
        </div>

        <RoundedFrame id="chat-frame" title="Chat">
          <div ref={chatLogRef} className="chat-log">
            <div className="frame-content">
              {deferredBlocks.map((block) => {
                const isRunning = block.status === 'running' && block.id === activeBlockIdRef.current;
                return (
                  <TerminalBlock
                    key={block.id}
                    block={block}
                    streamingOutput={isRunning ? chatStream.displayedText : undefined}
                    isRunning={isRunning}
                    onEdit={(cmd) => setInputValue(cmd)}
                    theme={theme}
                  />
                );
              })}
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

      {/* Terminal panel — resizable input area with command history */}
      <TerminalPanel
        userCommands={userCommands}
        shellOutput={shellOutput}
        inputValue={inputValue}
        onInputChange={setInputValue}
        onSubmit={handleSubmit}
        disabled={inputDisabled}
        commandHistory={commandHistory}
        isLive={isLive}
        inputRef={inputRef}
      />

      {/* Status bar */}
      <StatusBar
        balanceDisplay={balanceDisplay}
        gitBranch={gitBranch}
        gitDirty={gitDirty}
        botStatus={botStatus}
        setBotStatus={setBotStatus}
        workspace={workspace}
        sessionName={sessionName}
        planSteps={planSteps}
        planDone={planDone}
        themeEntry={themeEntry}
        PALETTE_SVG={PALETTE_SVG}
        THEMES={THEMES}
        theme={theme}
        themePickerOpen={themePickerOpen}
        setThemePickerOpen={setThemePickerOpen}
        themeToggleRef={themeToggleRef}
        dropdownPos={dropdownPos}
        applyTheme={applyTheme}
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

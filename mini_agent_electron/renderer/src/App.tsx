import { useState, useRef, useEffect, useCallback, startTransition, useDeferredValue } from 'react';
import type { ChatBlock, ThinkingBlock, UserCommand, ShellOutputEntry, ToolCardData, BackendStatusData, BalanceData, StreamToolOutputData, StreamToolEndData } from './types';
import useSmoothStream from './hooks/useSmoothStream';
import useTheme from './hooks/useTheme';
import TerminalBlock from './components/TerminalBlock';
import ErrorBoundary from './components/ErrorBoundary';
import SettingsPanel from './components/SettingsPanel';
import ToolCard from './components/ToolCard';
import DeferredMarkdown from './components/DeferredMarkdown';
import Header from './components/Header';
import StatusBar from './components/StatusBar';
import TerminalPanel from './components/TerminalPanel';
import RoundedFrame from './components/RoundedFrame';
import AgentTree from './components/AgentTree';


// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
function AppShell() {
  // Terminal blocks -- Warp-style command+output blocks
  // Each block: { id, command, output, status, timestamp }
  // status: 'running' | 'ok' | 'err'
  const [blocks, setBlocks] = useState<ChatBlock[]>([]);

  // Command history for Up/Down navigation in ShellInput
  const [commandHistory, setCommandHistory] = useState<string[]>([]);
  // User commands shown in the terminal panel history (scrollable)
  const [userCommands, setUserCommands] = useState<UserCommand[]>([]);
  // Shell command output (from /sh) displayed in the terminal panel
  const [shellOutput, setShellOutput] = useState<ShellOutputEntry[]>([]);
  const activeBlockIdRef = useRef<number | null>(null);  // ID of the currently streaming block

  // Tool Cards state -- Dirac-inspired card-based display
  // Each card: { id, toolName, toolArgs, status, output, startTime, endTime, diffPreview, errorDetail }
  const [toolCards, setToolCards] = useState<ToolCardData[]>([]);
  const toolCardIdRef = useRef<number>(0);
  const toolCardIndexRef = useRef<Map<number, number>>(new Map()); // cardId -> array index for O(1) lookup

  // Deferred values keep the UI responsive during heavy streaming
  const deferredBlocks = useDeferredValue(blocks);

  // Sub-agent data -- { [task_id]: { name, desc, toolCalls: [], thoughts: [], output: "", ok: null } }
  const [subagentData, setSubagentData] = useState<Record<string, any>>({});
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
  const [restoredCount, setRestoredCount] = useState<number | null>(null);
  const [isLive, setIsLive] = useState(false);
  const [turnCountVal, setTurnCountVal] = useState<number | null>(null);
  const [elapsedSec, setElapsedSec] = useState<number | null>(null);
  const [inputDisabled, setInputDisabled] = useState(false);
  const [thinkingBlocks, setThinkingBlocks] = useState<ThinkingBlock[]>([]);
  const thinkingIdCounterRef = useRef(0);
  const deferredThinkingBlocks = useDeferredValue(thinkingBlocks);
  const [provider, setProvider] = useState('deepseek');

  // Reasonix-style status bar state
  const [balanceDisplay, setBalanceDisplay] = useState<BalanceData | null>(null);
  const [sessionCost, setSessionCost] = useState('-');
  const [turnCost, setTurnCost] = useState('-');
  const [cacheHitRate, setCacheHitRate] = useState<number | null>(null);
  const [subagentRunning, setSubagentRunning] = useState(0);
  // plan UI removed — plan feature doesn't work

  // Theme hook (extracted)
  const {
    theme, themeEntry, PALETTE_SVG, THEMES,
    themePickerOpen, setThemePickerOpen, themeToggleRef, dropdownPos,
    applyTheme, cycleTheme,
  } = useTheme();

  const inputRef = useRef<{ focus: () => void; blur: () => void } | null>(null);
  const thinkingLogRef = useRef<HTMLDivElement | null>(null);
  const chatLogRef = useRef<HTMLDivElement | null>(null);
  const toolsLogRef = useRef<HTMLDivElement | null>(null);
  const inThinkingRef = useRef(false);
  const submitTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const turnStartRef = useRef<number | null>(null);
  const toolOutputStack = useRef<Array<{ cardId: number; buffer: string; toolName: string; toolCallId?: string }>>([]); // stack of buffers for parallel tool calls
  const orphanOutputs = useRef<Array<{ toolName: string; toolCallId?: string; lines: string[] }>>([]); // buffered output lines before tool_start arrives
  const orphanTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null); // timeout to flush orphans
  const lineIdRef = useRef(0); // monotonically increasing ID for stable React keys
  const nextLineId = useCallback(() => ++lineIdRef.current, []);

  const startTimer = useCallback(() => {
    if (timerRef.current) return; // already running
    turnStartRef.current = Date.now();
    setElapsedSec(0);
    timerRef.current = setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - turnStartRef.current!) / 1000));
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

    const onStatus = (data: BackendStatusData) => {
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
      // plan_steps/plan_done ignored — plan UI removed
    };
    const unsub = api.on('backend:status', onStatus);
    api.getStatus?.().then((data) => { if (data) onStatus(data); });
    return () => unsub();
  // Dependencies intentionally left minimal — this effect subscribes once on mount.
  // React guarantees state setters are stable; api is a global reference.
  }, []);

  // Stream listeners
  useEffect(() => {
    const api = window.miniAgent;
    if (!api) return;

    const unsubs: Array<() => void> = [];

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
      if (flushed) {
        const id = ++thinkingIdCounterRef.current;
        startTransition(() => setThinkingBlocks((prev) => [...prev.slice(-49), { id, text: flushed, timestamp: Date.now() }]));
      }
    }));

    unsubs.push(api.on('stream:tool_start', (data) => {
      // Prefer explicit tool_name from backend; fall back to parsing summary
      // Parse toolName and toolArgs from summary, handling nested parentheses
      const toolName = data.tool_name || (() => {
        const s = data.summary || '?';
        const parenIdx = s.indexOf('(');
        return parenIdx > 0 ? s.slice(0, parenIdx) : s;
      })();
      const toolArgs = (() => {
        const s = data.summary || '';
        const parenIdx = s.indexOf('(');
        if (parenIdx <= 0) return '';
        // Walk to find matching closing paren, handling nested parens
        let depth = 0;
        let closeIdx = -1;
        for (let i = parenIdx; i < s.length; i++) {
          if (s[i] === '(') depth++;
          else if (s[i] === ')') { depth--; if (depth === 0) { closeIdx = i; break; } }
        }
        return closeIdx > 0 ? s.slice(parenIdx, closeIdx + 1) : s.slice(parenIdx);
      })();
      const cardId = ++toolCardIdRef.current;
      // Set a data-enter attribute for CSS animation targeting
      // (avoids nth-child animation churn on new card insertion)
      const newCard: ToolCardData = {
        id: cardId, toolName, toolCallId: data.tool_call_id || '', toolArgs, status: 'running', output: '',
        startTime: Date.now(), endTime: null, diffPreview: null, errorDetail: null,
      };
      startTransition(() => {
        setToolCards((prev) => {
          // Cap at 50 cards to prevent unbounded growth
          const capped = prev.length >= 50 ? prev.slice(prev.length - 49) : prev;
          // Clean up index entries for pruned cards
          if (prev.length >= 50) {
            const keptIds = new Set(capped.map((c) => c.id));
            for (const id of toolCardIndexRef.current.keys()) {
              if (!keptIds.has(id)) toolCardIndexRef.current.delete(id);
            }
          }
          const idx = capped.length;
          toolCardIndexRef.current.set(cardId, idx);
          return [...capped, cardWithEnter];
        });
      });
      toolOutputStack.current.push({ cardId, buffer: '', toolName, toolCallId: data.tool_call_id || '' });

      // Drain any orphan output lines buffered before tool_start arrived
      const orphans = orphanOutputs.current;
      const matchedOrphans = orphans.filter((o) => {
        if (data.tool_call_id && o.toolCallId) return o.toolCallId === data.tool_call_id;
        return o.toolName === toolName;
      });
      if (matchedOrphans.length > 0) {
        const entry = toolOutputStack.current.find((e) => e.cardId === cardId);
        if (entry) {
          const merged = matchedOrphans.flatMap((o) => o.lines).join('');
          entry.buffer += merged;
          startTransition(() => {
            setToolCards((prev) => {
              const idx = toolCardIndexRef.current.get(cardId);
              if (idx == null) return prev;
              const card = prev[idx];
              if (!card || card.id !== cardId) return prev;
              const updated = [...prev];
              updated[idx] = { ...card, output: entry.buffer };
              return updated;
            });
          });
        }
        // Remove consumed orphans
        orphanOutputs.current = orphans.filter((o) => {
          if (data.tool_call_id && o.toolCallId) return o.toolCallId !== data.tool_call_id;
          return o.toolName !== toolName;
        });
      }

      // Clear orphan flush timeout if no orphans remain
      if (orphanOutputs.current.length === 0 && orphanTimeoutRef.current) {
        clearTimeout(orphanTimeoutRef.current);
        orphanTimeoutRef.current = null;
      }
    }));

    unsubs.push(api.on('stream:tool_output', (rawData) => {
      const data = rawData as StreamToolOutputData;
      const stack = toolOutputStack.current;
      const tName = data.tool_name || '';
      const tCallId = data.tool_call_id || '';

      // If stack is empty, buffer as orphan — tool_start hasn't arrived yet (IPC race)
      if (stack.length === 0) {
        if (tName) {
          // Match by tool_call_id first (unique), then toolName
          let orphan: { toolName: string; toolCallId?: string; lines: string[] } | undefined;
          if (tCallId) {
            orphan = orphanOutputs.current.find(
              (o) => o.toolCallId === tCallId || o.toolName === tName
            );
          } else {
            orphan = orphanOutputs.current.find((o) => o.toolName === tName);
          }
          if (!orphan) {
            orphan = { toolName: tName, toolCallId: tCallId || undefined, lines: [] };
            orphanOutputs.current.push(orphan);
          }
          orphan.lines.push(data.line || '');
          // Set a 5s timeout to flush orphans as a safety net
          if (!orphanTimeoutRef.current) {
            orphanTimeoutRef.current = setTimeout(() => {
              orphanOutputs.current.length = 0;
              orphanTimeoutRef.current = null;
            }, 5000);
          }
        }
        return;
      }

      // Match by tool_call_id first (unique, handles same-toolName batches), then tool_name, then LIFO
      let top: { cardId: number; buffer: string; toolName: string; toolCallId: string } | undefined;
      if (tCallId) {
        const found = stack.find((e) => e.toolCallId === tCallId);
        if (found) top = found as typeof top;
      }
      if (!top && tName) {
        const found = stack.find((e) => e.toolName === tName);
        if (found) top = found as { cardId: number; buffer: string; toolName: string; toolCallId: string };
      }
      if (!top) top = stack[stack.length - 1] as { cardId: number; buffer: string; toolName: string; toolCallId: string };
      if (!top) return; // stack exhausted, nothing to match
      top.buffer += data.line || '';
      startTransition(() => {
        setToolCards((prev) => {
          const idx = toolCardIndexRef.current.get(top.cardId);
          if (idx == null) return prev; // card removed, skip
          const card = prev[idx];
          if (!card || card.id !== top.cardId) return prev;
          const updated = [...prev];
          updated[idx] = { ...card, output: top.buffer };
          return updated;
        });
      });
    }));

    unsubs.push(api.on('stream:tool_end', (rawData) => {
      const data = rawData as StreamToolEndData;
      const stack = toolOutputStack.current;
      const tName = data.tool_name || '';

      // Guard: if tool_end fires without a matching tool_start
      const tCallId = data.tool_call_id || '';
      if (stack.length === 0 && (tCallId || tName)) {
        // Fallback: search cards directly for a running card with this tool_call_id or tool_name.
        startTransition(() => {
          setToolCards((prev) => {
            let matchIdx = -1;
            for (let i = prev.length - 1; i >= 0; i--) {
              if (prev[i].status === 'running' && (
                (tCallId && prev[i].toolCallId === tCallId) ||
                (!tCallId && prev[i].toolName === tName)
              )) {
                matchIdx = i;
                break;
              }
            }
            if (matchIdx === -1) return prev;
            const matched = prev[matchIdx];
            const code = data.content || '';
            const diffPreview = data.diff_preview || null;
            const errorDetail = !data.ok ? (data.detail || '') : '';
            const status = data.ok ? 'ok' : 'err';
            const updated = [...prev];
            updated[matchIdx] = { ...matched, status, endTime: Date.now(), output: code, diffPreview, errorDetail };
            return updated;
          });
        });
        return;
      }

      let cardId: number | null = null;
      let finalBuffer = '';
      // Match by tool_call_id first (unique, handles same-toolName batches), then tool_name, then LIFO
      if (tCallId) {
        const idx = stack.findIndex((e) => e.toolCallId === tCallId);
        if (idx !== -1) {
          const entry = stack[idx];
          finalBuffer = entry.buffer;
          cardId = entry.cardId;
          stack.splice(idx, 1);
        }
      }
      // When tool_call_id didn't match (or was absent), try tool_name.
      // If tCallId IS present but no stack entry matched by ID, prefer
      // entries with an empty toolCallId -- those were created by a
      // tool_start that fired before the streaming ID arrived.
      if (cardId == null && tName) {
        let idx = -1;
        if (tCallId) {
          // Prefer empty toolCallId entries (ID arrived late), then any match
          idx = stack.findIndex((e) => e.toolName === tName && !e.toolCallId);
          if (idx === -1) {
            idx = stack.findIndex((e) => e.toolName === tName);
          }
        } else {
          idx = stack.findIndex((e) => e.toolName === tName);
        }
        if (idx !== -1) {
          const entry = stack[idx];
          // Heal: if tool_end has a tool_call_id but the stack entry doesn't,
          // update the card so future lookups (e.g. card fallback) can match.
          if (tCallId && !entry.toolCallId) {
            entry.toolCallId = tCallId;
            startTransition(() => {
              setToolCards((prev) => {
                const ci = toolCardIndexRef.current.get(entry.cardId);
                if (ci == null) return prev;
                const c = prev[ci];
                if (!c || c.id !== entry.cardId) return prev;
                const u = [...prev];
                u[ci] = { ...c, toolCallId: tCallId };
                return u;
              });
            });
          }
          finalBuffer = entry.buffer;
          cardId = entry.cardId;
          stack.splice(idx, 1);
        }
      }
      if (cardId == null && stack.length > 0) {
        // Fallback: LIFO pop (sequential execution, or tool_name not available)
        const top = stack.pop()!;
        finalBuffer = top.buffer;
        cardId = top.cardId;
      }

      if (cardId == null) {
        // Stack is out of sync with running cards — fall back to searching
        // all running cards by tool_call_id first, then tool_name.
        if (tCallId || tName) {
          startTransition(() => {
            setToolCards((prev) => {
              let matchIdx = -1;
              for (let i = prev.length - 1; i >= 0; i--) {
                if (prev[i].status === 'running' && (
                  (tCallId && prev[i].toolCallId === tCallId) ||
                  (!tCallId && prev[i].toolName === tName)
                )) {
                  matchIdx = i;
                  break;
                }
              }
              if (matchIdx === -1) {
                console.warn('[App] tool_end for "%s" (call_id: %s) could not be matched to any running card', tName, tCallId);
                return prev;
              }
              const matched = prev[matchIdx];
              const now = Date.now();
              const status = data.ok ? 'ok' : 'err';
              const code = data.content || matched.output || '';
              const diffPreview = data.diff_preview || null;
              const errorDetail = !data.ok ? (data.detail || '') : '';
              const { _enter, ...clean } = matched;
              const updated = [...prev];
              updated[matchIdx] = { ...clean, status, endTime: now, output: code, diffPreview, errorDetail };
              return updated;
            });
          });
        } else {
          // No tool_name or tool_call_id — match any running card (most recent first)
          startTransition(() => {
            setToolCards((prev) => {
              let matchIdx = -1;
              for (let i = prev.length - 1; i >= 0; i--) {
                if (prev[i].status === 'running') {
                  matchIdx = i;
                  break;
                }
              }
              if (matchIdx === -1) return prev;
              const matched = prev[matchIdx];
              const now = Date.now();
              const status = data.ok ? 'ok' : 'err';
              const code = data.content || matched.output || '';
              const diffPreview = data.diff_preview || null;
              const errorDetail = !data.ok ? (data.detail || '') : '';
              const { _enter, ...clean } = matched;
              const updated = [...prev];
              updated[matchIdx] = { ...clean, status, endTime: now, output: code, diffPreview, errorDetail };
              return updated;
            });
          });
        }
        return;
      }

      const now = Date.now();
      const status = data.ok ? 'ok' : 'err';
      const code = finalBuffer || data.content || '';
      const diffPreview = data.diff_preview || null;
      const errorDetail = !data.ok ? (data.detail || '') : '';
      startTransition(() => {
        setToolCards((prev) => {
          const idx = toolCardIndexRef.current.get(cardId);
          if (idx == null) return prev;
          const card = prev[idx];
          if (!card || card.id !== cardId) return prev;
          const updated = [...prev];
          updated[idx] = { ...card, status, endTime: now, output: code, diffPreview, errorDetail };
          return updated;
        });
      });
    }));

    unsubs.push(api.on('stream:turn_complete', (data) => {
      clearTimeout(submitTimeoutRef.current!);
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
      // plan_steps/plan_done ignored — plan UI removed
      setIsLive(false);
      setInputDisabled(false);
      inputRef.current?.focus();
      // Final sweep: mark any cards still "running" as ok (turn done = all tools done)
      setToolCards((prev) => {
        let changed = false;
        const updated = prev.map((card) => {
          if (card.status === 'running') {
            changed = true;
            return { ...card, status: 'ok' as const, endTime: Date.now() };
          }
          return card;
        });
        return changed ? updated : prev;
      });
    }));

    unsubs.push(api.on('stream:error', (data) => {
      clearTimeout(submitTimeoutRef.current!);
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
        // Clean up any running tool cards on error
        toolOutputStack.current.length = 0;
        setToolCards((prev) =>
          prev.map((card) =>
            card.status === 'running' ? { ...card, status: 'err', endTime: Date.now(), errorDetail: data.message } : card
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
        setBlocks((prev) => [...prev.slice(-199), {
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
            setBlocks((prev) => [...prev.slice(-199), {
              id: nextLineId(), command: data.command || '', output, status: blockStatus, timestamp: Date.now(),
            }]);
          }
        });
        // Re-enable input after slash commands complete
        clearTimeout(submitTimeoutRef.current!);
        setIsLive(false);
        setInputDisabled(false);
        inputRef.current?.focus();
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
      // Re-enable input when /sh command completes (exit_code signals final message)
      if (data.exit_code !== undefined) {
        clearTimeout(submitTimeoutRef.current!);
        setIsLive(false);
        setInputDisabled(false);
        inputRef.current?.focus();
      }
    }));

    unsubs.push(api.on('backend:turn_start', () => {
      setIsLive(true);
      startTimer();
    }));

    unsubs.push(api.on('backend:idle', () => {
      clearTimeout(submitTimeoutRef.current!);
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
  // Dependencies intentionally left minimal — this effect subscribes once on mount.
  // State setters from React are stable; api, startTimer, stopTimer are refs/globals.
  }, []);

  // Submit handler -- creates terminal blocks, supports slash commands.
  // Regular text: if a turn is running, queue as an interjection;
  // otherwise start a new turn with a 'running' block.
  const handleSubmit = useCallback((text: string) => {
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
      setIsLive(true);
      setInputDisabled(true);
      // All slash commands create a block in the chat area (including /sh)
      const cmdId = nextLineId();
      startTransition(() => {
        setBlocks((prev) => [...prev.slice(-199), {
          id: cmdId, command: trimmed, output: '', status: 'running', timestamp: Date.now(),
        }]);
      });
      activeBlockIdRef.current = cmdId;
      window.miniAgent?.command(trimmed);
      submitTimeoutRef.current = setTimeout(() => {
        setInputDisabled(false);
        inputRef.current?.focus();
      }, 120_000);
      return;
    }

    // Regular message -- create a block
    const blockId = nextLineId();
    const blockStatus = isLive ? 'ok' : 'running';  // interjections are info blocks
    startTransition(() => {
      setBlocks((prev) => [...prev.slice(-199), {
        id: blockId, command: trimmed,
        output: isLive ? '(queued)' : '',
        status: blockStatus, timestamp: Date.now(),
      }]);
    });
    setInputValue('');

    if (isLive) {
      // Agent is running -- queue as interjection, don't set activeBlockId
      window.miniAgent?.interject(trimmed);
      inputRef.current?.focus();
    } else {
      // Agent is idle -- start a new turn
      activeBlockIdRef.current = blockId;
      chatStream.reset();
      setIsLive(true);
      // Don't disable input — user can type interjections while agent is running
      window.miniAgent?.submit(trimmed);

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
  const handleSessionSwitch = useCallback((name: string, isNew?: boolean) => {
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
    clearTimeout(submitTimeoutRef.current ?? undefined);
    stopTimer();
    inThinkingRef.current = false;
    const agentText = chatStream.flush();
    const thinkText = thinking.flush();
    if (thinkText) {
      const id = ++thinkingIdCounterRef.current;
      startTransition(() => setThinkingBlocks((prev) => [...prev.slice(-49), { id, text: thinkText, timestamp: Date.now() }]));
    }
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
  }, [thinkingBlocks, thinking.displayedText]);

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
      clearTimeout(submitTimeoutRef.current!);
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
          <RoundedFrame id="tools-frame">
            <div ref={toolsLogRef} className="tools-log">
              <div className="frame-content">
                {toolCards.map((card) => (
                  <ToolCard key={card.id} tool={card} theme={theme} />
                ))}
              </div>
            </div>
          </RoundedFrame>

          <RoundedFrame id="thinking-frame">
            <div ref={thinkingLogRef} className="thinking-log">
              <div className="frame-content">
                {deferredThinkingBlocks.map((block) => (
                  <div key={block.id} className="thinking-block">
                    <div
                      className="thinking-block__header"
                      onClick={() => {
                        startTransition(() =>
                          setThinkingBlocks((prev) =>
                            prev.map((b) => (b.id === block.id ? { ...b, collapsed: !b.collapsed } : b))
                          )
                        );
                      }}
                      role="button"
                      tabIndex={0}
                    >
                      <span className="thinking-block__chevron">{block.collapsed ? '\u25b6' : '\u25bc'}</span>
                      <span className="thinking-block__time dim">{new Date(block.timestamp).toLocaleTimeString()}</span>
                    </div>
                    {!block.collapsed && (
                      <div className="thinking-block__body">
                        <DeferredMarkdown text={block.text} cls="thinking" />
                      </div>
                    )}
                  </div>
                ))}
                {thinking.displayedText && (
                  <div className="thinking-block thinking-block--live">
                    <div className="thinking-block__header">
                      <span className="thinking-block__chevron">{'\u25bc'}</span>
                      <span className="thinking-block__time dim">streaming...</span>
                    </div>
                    <div className="thinking-block__body">
                      <DeferredMarkdown text={thinking.displayedText} cls="thinking" />
                    </div>
                  </div>
                )}
              </div>
            </div>
          </RoundedFrame>
        </div>

        <RoundedFrame id="chat-frame">
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
          <RoundedFrame id="subagents-frame">
            <AgentTree agents={deferredSubagentData} />
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

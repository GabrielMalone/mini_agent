// ---------------------------------------------------------------------------
// Shared TypeScript types for the mini_agent Electron renderer
// ---------------------------------------------------------------------------

import type React from 'react';

// ---- Stream / IPC event payloads -------------------------------------------

export interface StreamTokenData {
  text: string;
}

export interface StreamToolStartData {
  summary?: string;
  tool_name?: string;
  tool_call_id?: string;
  parallel?: boolean;
}

export interface StreamToolOutputData {
  line?: string;
  tool_name?: string;
}

export interface StreamToolEndData {
  ok: boolean;
  content?: string;
  diff_preview?: string | null;
  detail?: string;
  tool_name?: string;
  tool_call_id?: string;
}

export interface StreamTurnCompleteData {
  turn_count?: number;
  usage?: {
    turn_cost?: string;
    session_cost?: string;
    cache_hit_rate?: number;
    subagent_running?: number;
    balance?: BalanceData;
  };
}

export interface StreamErrorData {
  message: string;
}

export interface StreamSubagentStartData {
  agent_id: string;
  name?: string;
}

export interface StreamSubagentOutputData {
  agent_id: string;
  text?: string;
}

export interface StreamSubagentEndData {
  agent_id: string;
}

export interface StreamSubagentToolStartData {
  agent_id: string;
  tool_name?: string;
}

export interface StreamSubagentToolEndData {
  agent_id: string;
  tool_name?: string;
}

export interface StreamSubagentThoughtData {
  agent_id: string;
  text?: string;
}

export interface BackendStatusData {
  reason?: string;
  ready?: boolean;
  model?: string | null;
  provider?: string | null;
  session_name?: string | null;
  workspace?: string | null;
  git_branch?: string | null;
  git_dirty?: boolean;
  restored_count?: number | null;
  balance?: BalanceData;
  session_cost?: string;
  turn_cost?: string;
  cache_hit_rate?: number;
  subagent_running?: number;
}

export interface BackendBotStatusData {
  name: string;
  alive: boolean;
}

export interface BackendTurnStartData {
  turn_count?: number;
}

// ---- Balance ---------------------------------------------------------------

export interface BalanceData {
  available: boolean;
  display: string;
}

// ---- Session ---------------------------------------------------------------

export interface SessionListResult {
  sessions?: string[];
  current?: string;
  error?: string;
}

export interface SessionDeleteResult {
  ok: boolean;
  message?: string;
}

// ---- API Key ---------------------------------------------------------------

export interface ApiKeyStatusResult {
  configured: boolean;
  provider?: string;
}

// ---- Theme -----------------------------------------------------------------

export interface ThemeEntry {
  name: string;
  id: string;
  icon: React.ReactNode;
}

// ---- Model groups ----------------------------------------------------------

export interface ModelInfo {
  id: string;
  label: string;
}

export interface ModelGroup {
  group: string;
  models: ModelInfo[];
}

// ---- Blocks (chat history) -------------------------------------------------

export interface ToolCardData {
  id: number;
  toolName: string;
  toolCallId?: string;
  toolArgs: string;
  status: 'running' | 'ok' | 'err';
  output: string;
  startTime: number;
  endTime: number | null;
  diffPreview: string | null;
  errorDetail: string | null;
  _enter?: boolean;
}

export interface ChatBlock {
  id: number;
  command: string;
  output: string;
  status: 'ok' | 'err' | 'running';
  timestamp: number;
}

export interface ThinkingBlock {
  id: number;
  text: string;
  timestamp: number;
  collapsed?: boolean;
}

// ---- User commands / shell output ------------------------------------------

export interface UserCommand {
  id: number;
  text: string;
  timestamp: number;
}

export interface ShellOutputEntry {
  id: number;
  command: string;
  lines: string[];
  exitCode: number;
  timestamp: number;
}

// ---- Bot status ------------------------------------------------------------

export type BotStatusMap = Record<string, boolean>;

// ---- Dropdown position -----------------------------------------------------

export interface DropdownPosition {
  bottom?: number;
  top?: number;
  left?: number;
  right?: number;
}

// ---- Stream event channel names --------------------------------------------

export type StreamChannel =
  | 'stream:token'
  | 'stream:tool_start'
  | 'stream:tool_end'
  | 'stream:tool_output'
  | 'stream:thinking_start'
  | 'stream:thinking_end'
  | 'stream:turn_complete'
  | 'stream:error'
  | 'stream:status'
  | 'stream:shell_output'
  | 'stream:subagent_start'
  | 'stream:subagent_output'
  | 'stream:subagent_end'
  | 'stream:subagent_tool_start'
  | 'stream:subagent_tool_end'
  | 'stream:subagent_thought'
  | 'backend:status'
  | 'backend:response'
  | 'backend:turn_start'
  | 'backend:idle'
  | 'backend:bot_status';

// Generic unsubscriber
export type Unsubscribe = () => void;

// ---- window.miniAgent API surface ------------------------------------------

export interface MiniAgentAPI {
  submit: (text: string) => Promise<void>;
  command: (cmd: string) => Promise<void>;
  autocomplete: (text: string) => Promise<string[]>;
  cancel: () => Promise<void>;
  interject: (text: string) => Promise<void>;
  openWorkspace: () => Promise<string | null>;
  saveWorkspace: (path: string) => Promise<void>;
  listSessions: () => Promise<SessionListResult>;
  switchSession: (name: string) => Promise<void>;
  newSession: (name: string) => Promise<void>;
  deleteSession: (name: string) => Promise<SessionDeleteResult>;
  getStatus: () => Promise<BackendStatusData | null>;
  getApiKeyStatus: () => Promise<ApiKeyStatusResult>;
  saveApiKey: (provider: string, key: string) => Promise<void>;
  setModel: (model: string) => Promise<void>;
  getTheme: () => Promise<{ theme?: string }>;
  saveTheme: (themeId: string) => Promise<void>;
  restartBackend: () => Promise<void>;
  startBot: (script: string) => Promise<void>;
  stopBot: (script: string) => Promise<void>;
  onFileDrop: (callback: (paths: string[]) => void) => Unsubscribe;
  on: (channel: StreamChannel, callback: (data: any) => void) => Unsubscribe;
  removeAllListeners: (channel: StreamChannel) => void;
}

// ---- Globals ---------------------------------------------------------------

declare global {
  interface Window {
    miniAgent?: MiniAgentAPI;
  }
}

import { useRef, useState, useEffect, useCallback } from 'react';

// Model catalog for the clickable model picker dropdown
export const DIRECT_MODEL_GROUPS = [
  { group: 'DeepSeek', models: [
    { id: 'deepseek-v4-pro',   label: 'DeepSeek V4 Pro' },
    { id: 'deepseek-v4-flash', label: 'DeepSeek V4 Flash' },
  ]},
  { group: 'Kimi / Moonshot', models: [
    { id: 'kimi-k2.7-code', label: 'Kimi K2.7 Code' },
    { id: 'kimi-k2.6',      label: 'Kimi K2.6' },
  ]},
  { group: 'Qwen (DashScope)', models: [
    { id: 'qwen-plus',    label: 'Qwen-Plus' },
    { id: 'qwen-flash',   label: 'Qwen-Flash' },
    { id: 'qwen3-max',    label: 'Qwen 3 Max' },
    { id: 'qwen3-coder',  label: 'Qwen 3 Coder' },
  ]},
  { group: 'Free Tier', models: [
    { id: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash (free)' },
  ]},
];

export const OPENROUTER_MODEL_GROUPS = [
  { group: 'Kimi / Moonshot', models: [
    { id: 'moonshotai/kimi-k2.7-code', label: 'Kimi K2.7 Code' },
    { id: 'moonshotai/kimi-k2.6',      label: 'Kimi K2.6' },
  ]},
  { group: 'Google / Gemini', models: [
    { id: 'google/gemini-3.5-flash', label: 'Gemini 3.5 Flash' },
    { id: 'google/gemini-3.5-pro',   label: 'Gemini 3.5 Pro' },
  ]},
  { group: 'Qwen (DashScope)', models: [
    { id: 'qwen/qwen-plus',    label: 'Qwen-Plus' },
    { id: 'qwen/qwen3-max',    label: 'Qwen 3 Max' },
    { id: 'qwen/qwen3-coder',  label: 'Qwen 3 Coder' },
  ]},
  { group: 'Free Models', models: [
    { id: 'deepseek/deepseek-v4-flash:free',   label: 'DeepSeek V4 Flash (free)' },
    { id: 'qwen/qwen3-coder:free',             label: 'Qwen 3 Coder (free)' },
    { id: 'google/gemma-4-31b-it:free',        label: 'Gemma 4 31B (free)' },
    { id: 'openai/gpt-oss-120b:free',          label: 'GPT-OSS 120B (free)' },
    { id: 'meta-llama/llama-3.3-70b-instruct:free', label: 'Llama 3.3 70B (free)' },
    { id: 'openrouter/free',                   label: 'OpenRouter Free Router' },
  ]},
];

interface HeaderProps {
  modelName: string | null;
  cacheHitRate: number | null;
  subagentRunning: number;
  loading: boolean;
}

export default function Header({ modelName, cacheHitRate, subagentRunning, loading }: HeaderProps) {
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const modelRef = useRef<HTMLSpanElement | null>(null);
  const [modelDropdownPos, setModelDropdownPos] = useState<{ top: number; left: number } | null>(null);

  // Position the model dropdown relative to the header model span
  const recalcPosition = useCallback(() => {
    if (!modelRef.current) return;
    const rect = modelRef.current.getBoundingClientRect();
    const dropdownW = 240;
    let left = rect.left;
    if (left + dropdownW > window.innerWidth - 8) {
      left = Math.max(4, window.innerWidth - dropdownW - 8);
    }
    setModelDropdownPos({
      top: rect.bottom + 4,
      left,
    });
  }, []);

  useEffect(() => {
    if (!modelPickerOpen) {
      setModelDropdownPos(null);
      return;
    }
    recalcPosition();
    window.addEventListener('resize', recalcPosition);
    return () => window.removeEventListener('resize', recalcPosition);
  }, [modelPickerOpen, recalcPosition]);

  // Close model picker on outside click
  useEffect(() => {
    if (!modelPickerOpen) return;
    const close = (e: MouseEvent) => {
      if (!e.target.closest('.model-dropdown') && !e.target.closest('#header-model')) {
        setModelPickerOpen(false);
      }
    };
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [modelPickerOpen]);

  return (
    <div id="header" className="header">
      {/* Reasonix-style status bar items (left of model) */}
      <span className="statusbar-metrics">
        {cacheHitRate != null && (
          <span className="statusbar-metric statusbar-cache" title={`Cache hit rate: ${cacheHitRate}%`}>
            <span className="statusbar-metric-value">{cacheHitRate}%</span>
          </span>
        )}
        {subagentRunning > 0 && (
          <span className="statusbar-metric statusbar-subagents" title={`${subagentRunning} sub-agents running`}>
            <span className="statusbar-metric-icon">{'\u2225'}</span>
            <span className="statusbar-metric-value">{subagentRunning}</span>
          </span>
        )}
      </span>
      {loading ? (
        <span id="header-model" className="text header-loading" title="Backend starting...">
          <span className="tool-card-spinner" />
        </span>
      ) : (
        <span
          id="header-model"
          className="text clickable"
          ref={modelRef}
          onClick={() => setModelPickerOpen((p) => !p)}
          title="Click to switch model"
        >{modelName}</span>
      )}
      {modelPickerOpen && modelDropdownPos && (
        <div className="model-dropdown" style={modelDropdownPos} onClick={(e) => e.stopPropagation()}>
          {/* DIRECT API section */}
          <div className="model-dropdown-section">
            <div className="model-dropdown-header model-dropdown-section-header">{'\u2500\u2500 DIRECT API \u2500\u2500'}</div>
            {DIRECT_MODEL_GROUPS.map((grp, gi) => (
              <div key={`direct-${gi}`}>
                <div className="model-dropdown-subheader">{grp.group}</div>
                {grp.models.map((m) => {
                  const isCurrent = m.id === modelName;
                  return (
                    <div
                      key={m.id}
                      className={`model-dropdown-item${isCurrent ? ' model-current' : ''}`}
                      onClick={(e) => { e.stopPropagation(); setModelPickerOpen(false); window.miniAgent?.setModel(m.id); }}
                    >
                      <span className="model-name">{m.label}</span>
                      <span className="model-id dim">{m.id}</span>
                      {isCurrent && <span className="model-check">{'\u2713'}</span>}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>

          {/* OPENROUTER section */}
          <div className="model-dropdown-section">
            <div className="model-dropdown-header model-dropdown-section-header">{'\u2500\u2500 OPENROUTER \u2500\u2500'}</div>
            {OPENROUTER_MODEL_GROUPS.map((grp, gi) => (
              <div key={`or-${gi}`}>
                <div className="model-dropdown-subheader">{grp.group}</div>
                {grp.models.map((m) => {
                  const isCurrent = m.id === modelName;
                  return (
                    <div
                      key={m.id}
                      className={`model-dropdown-item${isCurrent ? ' model-current' : ''}`}
                      onClick={(e) => { e.stopPropagation(); setModelPickerOpen(false); window.miniAgent?.setModel(m.id); }}
                    >
                      <span className="model-name">{m.label}</span>
                      <span className="model-id dim">{m.id}</span>
                      {isCurrent && <span className="model-check">{'\u2713'}</span>}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

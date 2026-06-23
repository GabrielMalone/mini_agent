import SessionPicker from './SessionPicker';
import type { BalanceData, ThemeEntry, DropdownPosition } from '../types';

interface StatusBarProps {
  balanceDisplay: BalanceData | null;
  gitBranch: string;
  gitDirty: boolean;
  workspace: string;
  sessionName: string;
  themeEntry: ThemeEntry;
  PALETTE_SVG: React.ReactNode;
  THEMES: ThemeEntry[];
  theme: string;
  themePickerOpen: boolean;
  setThemePickerOpen: (v: boolean) => void;
  themeToggleRef: React.RefObject<HTMLElement | null>;
  dropdownPos: DropdownPosition | null;
  applyTheme: (id: string) => void;
  handleWorkspaceClick: () => void;
  handleSessionSwitch: (name: string, isNew?: boolean) => void;
}

export default function StatusBar({
  balanceDisplay, gitBranch, gitDirty,
  workspace, sessionName,
  themeEntry, PALETTE_SVG, THEMES, theme, themePickerOpen, setThemePickerOpen,
  themeToggleRef, dropdownPos, applyTheme,
  handleWorkspaceClick, handleSessionSwitch,
}: StatusBarProps) {

  return (
    <div id="status-bar" className="status-bar dim">
      <span id="git-status">
        {gitBranch && (<><svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.5" className="icon-sm"><path d="M3 4v6a2 2 0 0 0 2 2h2M7 12l-2-2 2-2M11 5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM3 4.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z"/></svg>{gitBranch}{gitDirty ? '*' : ''}</>)}
      </span>

      <span id="theme-toggle" ref={themeToggleRef} onClick={() => setThemePickerOpen((p) => !p)} title={`Theme: ${themeEntry.name}`}>
        {PALETTE_SVG}
        {themePickerOpen && dropdownPos && (
          <div className="theme-dropdown" style={dropdownPos} onClick={(e) => e.stopPropagation()}>
            {THEMES.map((t) => (
              <div
                key={t.id}
                className={`theme-dropdown-item${t.id === theme ? ' theme-current' : ''}`}
                onClick={(e) => { e.stopPropagation(); applyTheme(t.id); }}
              >
                <span className="theme-icon">{t.icon}</span>
                <span className="theme-name">{t.name}</span>
                {t.id === theme && <span className="theme-check"><svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2" className="icon-sm"><polyline points="3,8 6.5,11.5 13,5"/></svg></span>}
              </div>
            ))}
          </div>
        )}
      </span>
      {balanceDisplay && balanceDisplay.available && (
        <span className="statusbar-metric statusbar-balance" title="Wallet balance">
          <span className="statusbar-metric-value">{balanceDisplay.display}</span>
        </span>
      )}

      <div className="status-right">
        <span id="workspace-info" className="clickable" onClick={handleWorkspaceClick} title="Click to change workspace">{workspace}</span>
        <SessionPicker sessionName={sessionName} onSwitch={handleSessionSwitch} />
      </div>
    </div>
  );
}

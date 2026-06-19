import { useRef, useState, useEffect, useCallback } from 'react';
import SessionPicker from './SessionPicker';

const BOT_SCRIPTS = { 'mini-agent': 'workspace_bot.py', 'emotion-game': 'discord_bot.py' };

export default function StatusBar({
  balanceDisplay, gitBranch, gitDirty, botStatus, setBotStatus,
  workspace, sessionName,
  themeEntry, PALETTE_SVG, THEMES, theme, themePickerOpen, setThemePickerOpen,
  themeToggleRef, dropdownPos, applyTheme,
  handleWorkspaceClick, handleSessionSwitch,
}) {
  const [botMenuOpen, setBotMenuOpen] = useState(false);
  const botMenuToggleRef = useRef(null);
  const [botMenuPos, setBotMenuPos] = useState(null);

  const handleBotToggle = useCallback(async (botName) => {
    const api = window.miniAgent;
    if (!api) return;
    const script = BOT_SCRIPTS[botName];
    if (!script) return;
    const current = botStatus[botName];
    setBotStatus((prev) => ({ ...prev, [botName]: !current }));
    try {
      if (current) {
        await api.stopBot(script);
      } else {
        await api.startBot(script);
      }
    } catch (e) {
      setBotStatus((prev) => ({ ...prev, [botName]: current }));
    }
  }, [botStatus, setBotStatus]);

  // Position the bot menu relative to the bot indicators span
  useEffect(() => {
    if (!botMenuOpen || !botMenuToggleRef.current) {
      setBotMenuPos(null);
      return;
    }
    const rect = botMenuToggleRef.current.getBoundingClientRect();
    const menuW = 200;
    let left = rect.left;
    if (left + menuW > window.innerWidth - 8) {
      left = Math.max(4, window.innerWidth - menuW - 8);
    }
    setBotMenuPos({
      bottom: window.innerHeight - rect.top + 4,
      left,
    });
  }, [botMenuOpen]);

  // Close bot menu on outside click
  useEffect(() => {
    if (!botMenuOpen) return;
    const close = (e) => {
      if (!e.target.closest('.bot-menu') && !e.target.closest('.discord-label')) {
        setBotMenuOpen(false);
      }
    };
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [botMenuOpen]);

  return (
    <div id="status-bar" className="status-bar dim">
      <span id="git-status">
        {gitBranch && (<><svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.5" className="icon-sm"><path d="M3 4v6a2 2 0 0 0 2 2h2M7 12l-2-2 2-2M11 5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM3 4.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3z"/></svg>{gitBranch}{gitDirty ? '*' : ''}</>)}
      </span>
      <span className="bot-indicators" ref={botMenuToggleRef}>
        <span
          className="discord-label clickable"
          title="Discord bots"
          onClick={(e) => { e.stopPropagation(); setBotMenuOpen((p) => !p); }}
        ><svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" className="icon-sm"><path d="M19.27 5.33C17.94 4.71 16.5 4.26 15 4a.09.09 0 0 0-.07.03c-.18.33-.39.76-.53 1.09a16.09 16.09 0 0 0-4.8 0c-.14-.34-.35-.76-.54-1.09-.01-.02-.04-.03-.07-.03-1.5.26-2.93.71-4.27 1.33-.01 0-.02.01-.03.02-2.72 4.07-3.47 8.03-3.1 11.95 0 .02.01.04.03.05 1.8 1.32 3.53 2.12 5.24 2.65.03.01.06 0 .07-.02.4-.55.76-1.13 1.07-1.74.02-.04 0-.08-.04-.09-.57-.22-1.11-.48-1.64-.78-.04-.02-.04-.08-.01-.11.11-.08.22-.17.33-.25.02-.02.05-.02.07-.01 3.44 1.57 7.15 1.57 10.55 0 .02-.01.05-.01.07.01.11.09.22.17.33.26.04.03.04.09-.01.11-.52.31-1.07.56-1.64.78-.04.01-.05.06-.04.09.32.61.68 1.19 1.07 1.74.03.01.06.02.09.01 1.72-.53 3.45-1.33 5.25-2.65.02-.01.03-.03.03-.05.44-4.53-.73-8.46-3.1-11.95-.01-.01-.02-.02-.04-.02zM8.52 14.91c-1.03 0-1.89-.95-1.89-2.12s.84-2.12 1.89-2.12c1.06 0 1.9.96 1.89 2.12 0 1.17-.84 2.12-1.89 2.12zm6.97 0c-1.03 0-1.89-.95-1.89-2.12s.84-2.12 1.89-2.12c1.06 0 1.9.96 1.89 2.12 0 1.17-.83 2.12-1.89 2.12z"/></svg>Discord</span>
        {/* Bot action menu — shows both bots */}
        {botMenuOpen && botMenuPos && (
          <div className="bot-menu" style={botMenuPos} onClick={(e) => e.stopPropagation()}>
            <div className="bot-menu-header">Discord Bots</div>
            {['mini-agent', 'emotion-game'].map((bn) => {
              const running = botStatus[bn];
              return (
                <div key={bn} className="bot-menu-item">
                  <div className="bot-menu-item-info">
                    <span className="bot-menu-item-name">{bn}</span>
                    <span className={`bot-menu-status ${running ? 'bot-menu-on' : 'bot-menu-off'}`}>
                      {running !== undefined ? (running ? 'Running' : 'Stopped') : '...'}
                    </span>
                  </div>
                  <button
                    className={`bot-menu-btn ${running ? 'bot-menu-stop' : 'bot-menu-start'}`}
                    onClick={() => { handleBotToggle(bn); }}
                  >{running ? 'Stop' : 'Start'}</button>
                </div>
              );
            })}
            <div className="bot-menu-actions">
              <button className="bot-menu-btn bot-menu-close" onClick={() => setBotMenuOpen(false)}>Close</button>
            </div>
          </div>
        )}
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

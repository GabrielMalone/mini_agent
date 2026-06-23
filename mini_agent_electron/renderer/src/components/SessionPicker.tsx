import { useState, useRef, useEffect, useCallback } from 'react';

interface SessionPickerProps {
  sessionName?: string;
  onSwitch: (name: string, isNew?: boolean) => void;
}

export default function SessionPicker({ sessionName, onSwitch }: SessionPickerProps) {
  const [open, setOpen] = useState(false);
  const [sessions, setSessions] = useState<string[]>([]);
  const [current, setCurrent] = useState(sessionName || 'default');
  const [showNewInput, setShowNewInput] = useState(false);
  const [newName, setNewName] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const dropdownRef = useRef<HTMLSpanElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const currentRef = useRef(current);
  currentRef.current = current;

  useEffect(() => {
    if (sessionName) setCurrent(sessionName);
  }, [sessionName]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
        setShowNewInput(false);
        setNewName('');
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  useEffect(() => {
    if (showNewInput && inputRef.current) {
      inputRef.current.focus();
    }
  }, [showNewInput]);

  const toggleOpen = useCallback(async () => {
    if (open) {
      setOpen(false);
      setShowNewInput(false);
      setNewName('');
      return;
    }
    setOpen(true);
    setError('');
    setLoading(true);
    try {
      const api = window.miniAgent;
      if (api && api.listSessions) {
        const result = await api.listSessions();
        if (result.error) {
          setError(result.error);
        } else {
          setSessions(result.sessions || []);
          setCurrent(result.current || 'default');
        }
      }
    } catch (_e) {
      setError('Failed to load sessions');
    } finally {
      setLoading(false);
    }
  }, [open]);

  const handleSelect = useCallback((name: string) => {
    setOpen(false);
    setShowNewInput(false);
    setNewName('');
    onSwitch(name);
  }, [onSwitch]);

  const handleDelete = useCallback(async (e: React.MouseEvent, name: string) => {
    e.stopPropagation();
    if (!window.confirm(`Delete session "${name}"? This cannot be undone.`)) return;
    const api = window.miniAgent;
    if (!api || !api.deleteSession) return;
    try {
      const result = await api.deleteSession(name);
      if (result.ok) {
        setSessions((prev) => prev.filter((s) => s !== name));
        if (name === currentRef.current) {
          setCurrent('default');
        }
      } else {
        setError(result.message || 'Delete failed');
      }
    } catch (_e) {
      setError('Delete failed');
    }
  }, [current]);

  const handleNewSubmit = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      const name = newName.trim();
      if (!name) return;
      setOpen(false);
      setShowNewInput(false);
      setNewName('');
      onSwitch(name, true);
    } else if (e.key === 'Escape') {
      setShowNewInput(false);
      setNewName('');
    }
  }, [newName, onSwitch]);

  const handleNewClick = useCallback(() => {
    setShowNewInput(true);
  }, []);

  return (
    <span id="header-session" className="session-picker dim" ref={dropdownRef}>
      <span className="session-label clickable" onClick={toggleOpen} title="Click to manage sessions">
        {current}
      </span>
      {open && (
        <div className="session-dropdown">
          {error && <div className="session-dropdown-error">{error}</div>}
          {loading && <div className="session-dropdown-loading dim">loading...</div>}
          {!loading && !error && sessions.length === 0 && (
            <div className="session-dropdown-empty dim">no sessions</div>
          )}
          {!loading && !error && sessions.map((s) => (
            <div
              key={s}
              className={`session-dropdown-item${s === current ? ' session-current' : ''}`}
              onClick={() => handleSelect(s)}
            >
              {s === current && <span className="session-check">V </span>}
              <span className="session-name">{s}</span>
              <button className="session-delete-btn" onClick={(e) => handleDelete(e, s)} title={`Delete "${s}"`} aria-label={`Delete session ${s}`}>x</button>
            </div>
          ))}
          <div className="session-dropdown-divider" />
          {showNewInput ? (
            <div className="session-dropdown-item session-new-input">
              <input
                ref={inputRef}
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={handleNewSubmit}
                placeholder="session name..."
                className="session-new-field"
              />
            </div>
          ) : (
            <div className="session-dropdown-item session-new-item" onClick={handleNewClick}>
              + New session...
            </div>
          )}
        </div>
      )}
    </span>
  );
}

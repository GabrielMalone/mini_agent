import { useState, useRef, useEffect, useCallback } from 'react';
import type { DropdownPosition, ThemeEntry } from '../types';

const PALETTE_SVG = <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="5" cy="8" r="2.5"/><circle cx="12" cy="4" r="2"/><circle cx="12" cy="11.5" r="2"/><path d="M3 13a3 3 0 0 0 5.2-2 1.8 1.8 0 0 1 2.1-1.8A3 3 0 0 0 13 6"/></svg>;

const THEME_COLORS: Record<string, string> = {
  dark:         '#a0a8c0',
  light:        '#e8ac4a',
  dracula:      '#bd93f9',
  nord:         '#88c0d0',
  catppuccin:   '#cba6f7',
  'rose-pine':  '#ebbcba',
  gruvbox:      '#d79921',
  solarized:    '#2aa198',
  'tokyo-night':'#7aa2f7',
  monokai:      '#a6e22e',
  'one-dark':         '#61afef',
  'github-dark':       '#58a6ff',
  'night-owl':         '#82aaff',
  everforest:          '#a7c080',
  'ayu-mirage':        '#ffcc66',
  'shades-of-purple':  '#fad000',
  synthwave:           '#ff7edb',
  kanagawa:            '#7e9cd8',
};

export const THEMES: ThemeEntry[] = [
  { name: 'Dark',         id: 'dark',         icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS.dark}/></svg> },
  { name: 'Light',        id: 'light',        icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS.light}/></svg> },
  { name: 'Dracula',      id: 'dracula',      icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS.dracula}/></svg> },
  { name: 'Nord',         id: 'nord',         icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS.nord}/></svg> },
  { name: 'Catppuccin',   id: 'catppuccin',   icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS.catppuccin}/></svg> },
  { name: 'Rose Pine',    id: 'rose-pine',    icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS['rose-pine']}/></svg> },
  { name: 'Gruvbox',      id: 'gruvbox',      icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS.gruvbox}/></svg> },
  { name: 'Solarized',    id: 'solarized',    icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS.solarized}/></svg> },
  { name: 'Tokyo Night',  id: 'tokyo-night',  icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS['tokyo-night']}/></svg> },
  { name: 'Monokai',      id: 'monokai',      icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS.monokai}/></svg> },
  { name: 'One Dark Pro',       id: 'one-dark',         icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS['one-dark']}/></svg> },
  { name: 'GitHub Dark',         id: 'github-dark',      icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS['github-dark']}/></svg> },
  { name: 'Night Owl',           id: 'night-owl',        icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS['night-owl']}/></svg> },
  { name: 'Everforest',          id: 'everforest',       icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS.everforest}/></svg> },
  { name: 'Ayu Mirage',          id: 'ayu-mirage',       icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS['ayu-mirage']}/></svg> },
  { name: 'Shades of Purple',    id: 'shades-of-purple', icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS['shades-of-purple']}/></svg> },
  { name: 'Synthwave 84',        id: 'synthwave',        icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS.synthwave}/></svg> },
  { name: 'Kanagawa',            id: 'kanagawa',         icon: <svg viewBox="0 0 12 12" width="10" height="10"><circle cx="6" cy="6" r="4" fill={THEME_COLORS.kanagawa}/></svg> },
];

function setThemeDom(id: string): void {
  document.documentElement.setAttribute('data-theme', id);
  localStorage.setItem('mini_agent_theme', id);
}

export interface UseThemeReturn {
  theme: string;
  themeEntry: ThemeEntry;
  themeIndex: number;
  PALETTE_SVG: React.ReactNode;
  THEMES: ThemeEntry[];
  themePickerOpen: boolean;
  setThemePickerOpen: React.Dispatch<React.SetStateAction<boolean>>;
  themeToggleRef: React.RefObject<HTMLElement | null>;
  dropdownPos: DropdownPosition | null;
  applyTheme: (id: string) => void;
  cycleTheme: () => void;
}

export default function useTheme(): UseThemeReturn {
  const [theme, setTheme] = useState<string>(() => {
    const stored = localStorage.getItem('mini_agent_theme');
    if (stored && THEMES.some((t) => t.id === stored)) {
      document.documentElement.setAttribute('data-theme', stored);
      return stored;
    }
    document.documentElement.setAttribute('data-theme', 'dark');
    return 'dark';
  });
  const [themePickerOpen, setThemePickerOpen] = useState(false);
  const themeToggleRef = useRef<HTMLElement | null>(null);
  const [dropdownPos, setDropdownPos] = useState<DropdownPosition | null>(null);

  const themeIndex = THEMES.findIndex((t) => t.id === theme);
  const themeEntry = THEMES[themeIndex] || THEMES[0];

  const applyTheme = useCallback((id: string) => {
    setTheme(id);
    setThemeDom(id);
    setThemePickerOpen(false);
    window.miniAgent?.saveTheme?.(id);
  }, []);

  const cycleTheme = useCallback(() => {
    const nextIndex = (themeIndex + 1) % THEMES.length;
    applyTheme(THEMES[nextIndex].id);
  }, [themeIndex, applyTheme]);

  useEffect(() => {
    setThemeDom(theme);
  }, [theme]);

  useEffect(() => {
    (async () => {
      try {
        const result = await window.miniAgent?.getTheme?.();
        const fileTheme = result?.theme;
        if (fileTheme && THEMES.some((t) => t.id === fileTheme) && fileTheme !== theme) {
          applyTheme(fileTheme);
        }
      } catch (_) { /* preload not available yet */ }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!themePickerOpen) return;
    const close = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('.theme-dropdown') && !(e.target as HTMLElement).closest('#theme-toggle')) {
        setThemePickerOpen(false);
      }
    };
    document.addEventListener('click', close);
    return () => document.removeEventListener('click', close);
  }, [themePickerOpen]);

  useEffect(() => {
    if (!themePickerOpen || !themeToggleRef.current) {
      setDropdownPos(null);
      return;
    }
    const rect = themeToggleRef.current.getBoundingClientRect();
    const dropdownW = 190;
    let right = window.innerWidth - rect.right;
    if (right + dropdownW > window.innerWidth - 8) {
      right = Math.max(4, window.innerWidth - dropdownW - 8);
    }
    setDropdownPos({
      bottom: window.innerHeight - rect.top + 4,
      right,
    });
  }, [themePickerOpen]);

  return {
    theme, themeEntry, themeIndex, PALETTE_SVG, THEMES,
    themePickerOpen, setThemePickerOpen, themeToggleRef, dropdownPos,
    applyTheme, cycleTheme,
  };
}

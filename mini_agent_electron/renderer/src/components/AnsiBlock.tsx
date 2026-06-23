import { useMemo } from 'react';
import anser from 'anser';

interface AnsiBlockProps {
  text: string;
  style?: React.CSSProperties;
  className?: string;
}

const ANSI_COLORS: Record<string, React.CSSProperties | null> = {
  '0': null,
  '1': { fontWeight: 'bold' },
  '2': { opacity: 0.55 },
  '3': { fontStyle: 'italic' },
  '4': { textDecoration: 'underline' },
  '30': { color: '#111' },
  '31': { color: '#f44747' },
  '32': { color: '#6a9955' },
  '33': { color: '#dcdcaa' },
  '34': { color: '#569cd6' },
  '35': { color: '#c586c0' },
  '36': { color: '#4ec9b0' },
  '37': { color: '#d4d4d4' },
  '90': { color: '#666' },
  '91': { color: '#f44747' },
  '92': { color: '#6a9955' },
  '93': { color: '#dcdcaa' },
  '94': { color: '#569cd6' },
  '95': { color: '#c586c0' },
  '96': { color: '#4ec9b0' },
  '97': { color: '#e0e0e0' },
};

function ansiToElements(text: string): React.ReactNode {
  if (!text) return null;

  if (text.indexOf('\x1b') === -1) {
    return text;
  }

  try {
    const json = anser.ansiToJson(text, { use_classes: false });
    if (!json || json.length === 0) return text;

    return json.map((seg: { fg?: string; bg?: string; decoration?: string; content: string }, i: number) => {
      const style: React.CSSProperties = {};
      if (seg.fg && ANSI_COLORS[seg.fg]) {
        const c = ANSI_COLORS[seg.fg];
        if (c?.color) style.color = c.color as string;
      }
      if (seg.bg && ANSI_COLORS[seg.bg]) {
        const c = ANSI_COLORS[seg.bg];
        if (c?.color) style.backgroundColor = c.color as string;
      }
      if (seg.decoration === 'bold') style.fontWeight = 'bold';
      if (seg.decoration === 'dim') style.opacity = 0.55;
      if (seg.decoration === 'italic') style.fontStyle = 'italic';
      if (seg.decoration === 'underline') style.textDecoration = 'underline';

      if (Object.keys(style).length === 0) {
        return <span key={i}>{seg.content}</span>;
      }
      return <span key={i} style={style}>{seg.content}</span>;
    });
  } catch {
    return text.replace(/\x1b\[[0-9;]*m/g, '');
  }
}

export default function AnsiBlock({ text, style = {}, className = '' }: AnsiBlockProps) {
  const elements = useMemo(() => ansiToElements(text), [text]);

  if (elements === null) return null;
  if (typeof elements === 'string') {
    return <span style={{ whiteSpace: 'pre-wrap', ...style }} className={className}>{elements}</span>;
  }

  return (
    <span style={{ ...style, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }} className={className}>
      {elements}
    </span>
  );
}

export { ansiToElements };

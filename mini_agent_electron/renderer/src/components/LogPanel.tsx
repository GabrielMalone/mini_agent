import { useRef, useEffect, memo, type ReactNode } from 'react';
import LogLine, { type LogLineData } from './LogLine';

interface LogPanelProps {
  id?: string;
  className?: string;
  lines?: LogLineData[];
  children?: ReactNode;
}

const LogPanel = memo(function LogPanel({ id, className, lines, children }: LogPanelProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [lines, children]);
  return (
    <div id={id} ref={ref} className={`log ${className || ''}`}>
      {lines && lines.map((line, i) => <LogLine key={i} line={line} />)}
      {children}
    </div>
  );
});

export default LogPanel;

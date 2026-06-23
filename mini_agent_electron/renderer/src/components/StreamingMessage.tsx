import { useState, useEffect, useRef, memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface StreamingMessageProps {
  text: string;
}

const StreamingMessage = memo(function StreamingMessage({ text }: StreamingMessageProps) {
  const [throttled, setThrottled] = useState('');
  const lastUpdateRef = useRef(0);
  const pendingRef = useRef<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const now = performance.now();
    const elapsed = now - lastUpdateRef.current;

    if (elapsed >= 80) {
      lastUpdateRef.current = now;
      setThrottled(text);
    } else {
      pendingRef.current = text;
      if (!timerRef.current) {
        const remaining = 80 - elapsed;
        timerRef.current = setTimeout(() => {
          timerRef.current = null;
          lastUpdateRef.current = performance.now();
          if (pendingRef.current !== null) {
            setThrottled(pendingRef.current);
            pendingRef.current = null;
          }
        }, remaining);
      }
    }

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [text]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  if (!text || !text.trim()) return null;

  const isStreaming = text !== throttled;

  return isStreaming ? (
    <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', overflowWrap: 'break-word', margin: 0, fontFamily: 'inherit', fontSize: 'inherit' }}>
      {text}
    </div>
  ) : (
    <ReactMarkdown remarkPlugins={[remarkGfm]}>
      {throttled || text}
    </ReactMarkdown>
  );
});

export default StreamingMessage;

import { useState, useRef, useCallback, useEffect } from 'react';

export interface SmoothStreamOpts {
  factor?: number;
}

export interface SmoothStreamReturn {
  displayedText: string;
  addChunk: (text: string) => void;
  reset: () => void;
  flush: () => string;
}

export default function useSmoothStream(opts?: SmoothStreamOpts): SmoothStreamReturn {
  const factor = opts?.factor ?? 4;
  const [displayedText, setDisplayedText] = useState('');
  const fullRef = useRef('');
  const indexRef = useRef(0);
  const rafRef = useRef<number | null>(null);
  const tickRef = useRef<(() => void) | null>(null);
  const mountedRef = useRef(true);

  // Build the tick closure each render cycle. Guarded by mountedRef to
  // prevent HMR-stale callbacks from running after component teardown.
  tickRef.current = () => {
    if (!mountedRef.current) return;
    const full = fullRef.current;
    const behind = full.length - indexRef.current;
    if (behind <= 0) {
      rafRef.current = null;
      return;
    }
    const step = Math.max(1, Math.ceil(behind / factor));
    // Seek forward to the next word boundary so text never splits mid-word
    const target = Math.min(indexRef.current + step, full.length);
    const wordBreakRx = /[\s\n.,;:!?)\]}>]/;
    let boundary = target;
    for (let i = target; i < full.length && i < target + 12; i++) {
      if (wordBreakRx.test(full[i])) { boundary = i + 1; break; }
    }
    indexRef.current = boundary;
    if (!mountedRef.current) return;
    setDisplayedText(full.slice(0, indexRef.current));

    if (indexRef.current < full.length && mountedRef.current) {
      rafRef.current = requestAnimationFrame(tickRef.current!);
    } else {
      rafRef.current = null;
    }
  };

  const addChunk = useCallback((text: string) => {
    if (!text) return;
    fullRef.current += text;
    if (!rafRef.current) {
      rafRef.current = requestAnimationFrame(tickRef.current!);
    }
  }, []);

  const reset = useCallback(() => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    fullRef.current = '';
    indexRef.current = 0;
    setDisplayedText('');
  }, []);

  // Tear down RAF on unmount + mark unmounted so in-flight callbacks bail
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, []);

  const flush = useCallback((): string => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    const full = fullRef.current;
    indexRef.current = full.length;
    fullRef.current = '';
    setDisplayedText('');
    return full;
  }, []);

  return { displayedText, addChunk, reset, flush };
}

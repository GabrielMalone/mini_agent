import { useState, useRef, useCallback, useEffect } from 'react';

/**
 * useSmoothStream -- buffer incoming text chunks and animate them
 * with vsync-locked rendering via requestAnimationFrame (~60 fps).
 *
 * Uses exponential catch-up: each frame advances by ceil(behind / 4),
 * so the animation is fast when far behind and slows naturally as it
 * catches up -- no jarring discrete thresholds.
 *
 * rAF advantages over setTimeout:
 *  - Vsync-locked to display refresh rate (smoother)
 *  - Automatically pauses when the tab is hidden
 *  - Browser batches rAF callbacks before paint (less jank)
 *  - Better battery life on laptops
 *
 * Returns:
 *   displayedText  -- current visible text (animating)
 *   addChunk       -- call with each incoming token chunk
 *   reset          -- clear the stream
 *   flush          -- instantly display all buffered text
 */
export default function useSmoothStream() {
  const [displayedText, setDisplayedText] = useState('');
  const fullRef = useRef('');
  const indexRef = useRef(0);
  const rafRef = useRef(null);
  const tickRef = useRef(null);

  tickRef.current = () => {
    const full = fullRef.current;
    const behind = full.length - indexRef.current;
    if (behind <= 0) {
      rafRef.current = null;
      return;
    }
    // Smooth exponential catch-up: advance by ceil(behind / 4).
    // Far behind -> big jumps.  Close -> 1 char per frame.
    const step = Math.max(1, Math.ceil(behind / 4));
    indexRef.current = Math.min(indexRef.current + step, full.length);
    setDisplayedText(full.slice(0, indexRef.current));

    // Schedule next frame if still behind
    if (indexRef.current < full.length) {
      rafRef.current = requestAnimationFrame(tickRef.current);
    } else {
      rafRef.current = null;
    }
  };

  const addChunk = useCallback((text) => {
    if (!text) return;
    fullRef.current += text;
    if (!rafRef.current) {
      rafRef.current = requestAnimationFrame(tickRef.current);
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

  const flush = useCallback(() => {
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

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, []);

  return { displayedText, addChunk, reset, flush };
}

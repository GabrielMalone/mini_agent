import { useEffect, useRef, useState } from 'react';
import type { DropdownPosition } from '../types';

export default function useDropdownPosition(
  isOpen: boolean,
  menuWidth: number = 200
): [DropdownPosition | null, React.RefObject<HTMLElement | null>] {
  const toggleRef = useRef<HTMLElement | null>(null);
  const [pos, setPos] = useState<DropdownPosition | null>(null);

  useEffect(() => {
    if (!isOpen || !toggleRef.current) {
      setPos(null);
      return;
    }
    const rect = toggleRef.current.getBoundingClientRect();
    let left = rect.left;
    if (left + menuWidth > window.innerWidth - 8) {
      left = Math.max(4, window.innerWidth - menuWidth - 8);
    }
    setPos({
      bottom: window.innerHeight - rect.top + 4,
      left,
    });
  }, [isOpen, menuWidth]);

  return [pos, toggleRef];
}

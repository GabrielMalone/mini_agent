import { useEffect, useRef, useState } from 'react';

/**
 * useDropdownPosition — shared hook for computing dropdown menu position
 * relative to a toggle element.  Returns a position object for absolute/fixed
 * positioning and a ref to attach to the toggle element.
 *
 * Usage:
 *   const [menuPos, toggleRef] = useDropdownPosition(menuOpen, menuWidth);
 *   // menuPos is null when closed, {bottom, left} when open
 */
export default function useDropdownPosition(isOpen, menuWidth = 200) {
  const toggleRef = useRef(null);
  const [pos, setPos] = useState(null);

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

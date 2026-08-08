import { useEffect, useState } from 'react';

/** Match CSS media `(min-width: ${px}px)` — used for iPad split (≥900 per brief §6). */
export function useMinWidth(px: number): boolean {
  const query = `(min-width: ${px}px)`;
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return false;
    }
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return;
    }
    const mql = window.matchMedia(query);
    const onChange = () => setMatches(mql.matches);
    onChange();
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}

/** Brief §6: iPad / wide shell at ≥900px. */
export const IPAD_MIN_WIDTH = 900;

export function useIsIpadShell(): boolean {
  return useMinWidth(IPAD_MIN_WIDTH);
}

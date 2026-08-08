import { useCallback, useEffect, useState } from 'react';
import api from '@/lib/api';
import type { OperationsSnapshot } from './types';

/**
 * Shared read of GET /api/operations/snapshot for Mobile Ops Status / Controls.
 * Reuses the central Bearer JWT client (§8.1 / §8.3).
 */
export function useOperationsSnapshot(pollMs = 15_000) {
  const [snapshot, setSnapshot] = useState<OperationsSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await api.get<OperationsSnapshot>('/api/operations/snapshot');
      setSnapshot(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load operations snapshot');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), pollMs);
    return () => window.clearInterval(id);
  }, [pollMs, refresh]);

  return { snapshot, error, loading, refresh };
}

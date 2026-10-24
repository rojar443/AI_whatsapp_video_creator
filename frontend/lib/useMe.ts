"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, Me } from "./api";

export function useMe(opts: { required?: boolean } = {}) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setMe(await api<Me>("/api/me"));
    } catch (e) {
      setMe(null);
      if (opts.required && e instanceof ApiError && e.status === 401) {
        window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
      }
    } finally {
      setLoading(false);
    }
  }, [opts.required]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { me, loading, refresh };
}

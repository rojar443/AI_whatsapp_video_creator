"use client";

import { useEffect, useState } from "react";
import { api } from "./api";

export type PublicConfig = { dev_mode: boolean; registration_enabled: boolean; whatsapp_enabled: boolean; telegram_bot: string | null };

export function useConfig(): PublicConfig | null {
  const [cfg, setCfg] = useState<PublicConfig | null>(null);
  useEffect(() => {
    api<PublicConfig>("/api/config").then(setCfg).catch(() => {});
  }, []);
  return cfg;
}

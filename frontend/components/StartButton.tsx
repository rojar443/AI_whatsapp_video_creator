"use client";

import Link from "next/link";
import { useConfig } from "@/lib/useConfig";

export default function StartButton() {
  const cfg = useConfig();
  return cfg?.telegram_bot ? (
    <a className="btn tg" href={`https://t.me/${cfg.telegram_bot}`} target="_blank" rel="noreferrer">Open in Telegram</a>
  ) : (
    <Link className="btn" href="#how">How it works</Link>
  );
}

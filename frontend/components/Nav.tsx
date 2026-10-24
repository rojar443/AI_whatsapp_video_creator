"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, Me } from "@/lib/api";
import { useConfig } from "@/lib/useConfig";

export default function Nav() {
  const path = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [dev, setDev] = useState(false);
  const cfg = useConfig();

  useEffect(() => {
    api<Me>("/api/me").then(setMe).catch(() => setMe(null));
    api<{ dev_mode: boolean }>("/api/config").then((c) => setDev(c.dev_mode)).catch(() => {});
  }, [path]);

  const link = (href: string, label: string) => (
    <Link href={href} className={path === href || path.startsWith(href + "/") ? "active" : ""}>
      {label}
    </Link>
  );

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    window.location.href = "/";
  }

  return (
    <nav className="nav">
      <div className="container">
        <Link href="/" className="brand">
          <span className="brand-dot" aria-hidden /> ReelChat
        </Link>
        <div className="nav-links">
          {me ? (
            <>
              {link("/dashboard", "Videos")}
              {link("/account", "Account")}
              {dev && link("/dev/whatsapp", "WhatsApp simulator")}
              <button className="btn secondary small" onClick={logout}>
                Log out
              </button>
            </>
          ) : (
            <>
              {link("/#how", "How it works")}
              {dev && link("/dev/whatsapp", "Try the simulator")}
              {cfg?.registration_enabled && link("/login", "Log in")}
              {cfg?.registration_enabled && (
                <Link href="/register" className="btn small">
                  Get started
                </Link>
              )}
              {cfg?.telegram_bot && (
                <a href={`https://t.me/${cfg.telegram_bot}`} className="btn small" target="_blank" rel="noreferrer">
                  Open in Telegram
                </a>
              )}
            </>
          )}
        </div>
      </div>
    </nav>
  );
}

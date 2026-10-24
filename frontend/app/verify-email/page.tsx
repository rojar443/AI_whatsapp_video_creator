"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function VerifyEmail() {
  const [state, setState] = useState<"working" | "ok" | "error">("working");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token") || "";
    api("/api/auth/verify-email", { method: "POST", json: { token } })
      .then(() => setState("ok"))
      .catch((e) => {
        setState("error");
        setMsg(e.message);
      });
  }, []);

  return (
    <div className="container narrow card">
      <h1>Email verification</h1>
      {state === "working" && <p>Verifying…</p>}
      {state === "ok" && (
        <>
          <div className="alert ok">Your email is verified.</div>
          <Link className="btn" href="/dashboard">Link WhatsApp →</Link>
        </>
      )}
      {state === "error" && (
        <div className="alert error">
          {msg}. You can request a new link from your <Link href="/dashboard">dashboard</Link>.
        </div>
      )}
    </div>
  );
}

"use client";

import Link from "next/link";
import { useConfig } from "@/lib/useConfig";

export default function RegisterLink() {
  const cfg = useConfig();
  return cfg?.registration_enabled ? <Link className="btn secondary" href="/register">Create account</Link> : null;
}

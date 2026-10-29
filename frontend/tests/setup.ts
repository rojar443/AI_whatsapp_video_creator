import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  if (typeof document === "undefined") return; // node-environment tests (API route)
  cleanup();
  document.cookie.split(";").forEach((c) => {
    document.cookie = c.split("=")[0].trim() + "=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/";
  });
});

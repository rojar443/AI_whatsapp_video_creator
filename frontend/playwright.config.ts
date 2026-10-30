import { defineConfig, devices } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

// End-to-end smoke test: real API + Celery worker + throwaway Redis (SQLite, offline AI/TTS) + Next dev server.
const tmp = mkdtempSync(path.join(tmpdir(), "reelchat-e2e-"));
const backend = path.resolve(__dirname, "../backend");
const env = {
  APP_ENV: "development",
  SECRET_KEY: "dev-e2e-secret-key-0123456789",
  DATABASE_URL: `sqlite:///${tmp}/e2e.db`,
  TASK_BACKEND: "celery",
  REDIS_URL: "redis://127.0.0.1:6391/0",
  STORAGE_LOCAL_DIR: `${tmp}/storage`,
  WORK_DIR: `${tmp}/work`,
  PLAYBOOK_DIR: `${backend}/playbooks`,
  MUSIC_LIBRARY_DIR: `${tmp}/music`,
  MESSAGING_PROVIDER: "dev",
  WHATSAPP_ENABLED: "true",
  REGISTRATION_ENABLED: "true",
  AI_PROVIDER: "rules",
  TTS_PROVIDER: "mock",
  TRANSCRIPTION_PROVIDER: "mock",
  TELEGRAM_BOT_TOKEN: "",
  DEFAULT_CREDITS: "10",
  ANON_CREDITS: "2",
  PUBLIC_BASE_URL: "http://localhost:3100",
};

export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: "http://localhost:3100", trace: "retain-on-failure", ...devices["Desktop Chrome"] },
  webServer: [
    {
      command: `redis-server --port 6391 --save "" --appendonly no --dir ${tmp}`,
      port: 6391,
      reuseExistingServer: false,
    },
    {
      command: ".venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --port 8100",
      cwd: backend,
      env,
      url: "http://localhost:8100/api/health",
      timeout: 60_000,
      reuseExistingServer: false,
    },
    {
      command: ".venv/bin/celery -A app.jobs.celery_app worker -Q default,media --concurrency 1 --pool solo --loglevel INFO",
      cwd: backend,
      env,
      wait: { stdout: /celery@\S+ ready/ },
      reuseExistingServer: false,
    },
    {
      command: "npx next dev -p 3100",
      env: { BACKEND_URL: "http://localhost:8100" },
      url: "http://localhost:3100",
      timeout: 120_000,
      reuseExistingServer: false,
    },
  ],
});

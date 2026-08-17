import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

function normalizeBase(raw) {
  const value = (raw || "").trim() || "/";
  if (value === "/") return "/";
  const withLead = value.startsWith("/") ? value : `/${value}`;
  return withLead.endsWith("/") ? withLead : `${withLead}/`;
}

function resolveBase() {
  const fromEnv = (process.env.VITE_BASE || "").trim();
  if (fromEnv) return normalizeBase(fromEnv);
  if (process.env.GITHUB_ACTIONS && process.env.GITHUB_REPOSITORY) {
    const repo = process.env.GITHUB_REPOSITORY.split("/")[1];
    if (repo) return `/${repo}/`;
  }
  return "/";
}

function assertProductionApiBase(command) {
  if (command !== "build") return;
  const api = (process.env.VITE_API_BASE || "").trim();
  if (/localhost|127\.0\.0\.1/i.test(api)) {
    throw new Error(
      "VITE_API_BASE must not contain localhost or 127.0.0.1 in a production build. Use the public HTTPS API origin."
    );
  }
  if (!api) {
    console.warn(
      "\n[SellerOS] WARNING: VITE_API_BASE is empty.\n" +
        "The Mini App UI will still build, but Telegram auth and API calls will fail\n" +
        "until you set the GitHub Actions repository variable VITE_API_BASE to the Amvera HTTPS origin\n" +
        "(e.g. https://your-app.amvera.io).\n"
    );
  } else if (!/^https:\/\//i.test(api)) {
    console.warn(
      "[SellerOS] WARNING: VITE_API_BASE should be an absolute https:// origin with no trailing slash."
    );
  }
}

export default defineConfig(({ command }) => {
  assertProductionApiBase(command);
  return {
    base: resolveBase(),
    plugins: [react()],
    server: {
      port: 5175,
      strictPort: true,
      proxy: {
        "/dashboard": "http://127.0.0.1:8000",
        "/health": "http://127.0.0.1:8000",
        "/api": "http://127.0.0.1:8000",
      },
    },
  };
});

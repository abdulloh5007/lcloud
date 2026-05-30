import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import "./index.css";

/**
 * React Query defaults — tuned to feel snappy without hammering the server.
 *
 * - `staleTime: 30s` matches the backend `/clouds` and `/files` cache TTL,
 *   so within that window we serve from memory (no network).
 * - `gcTime: 5 min` keeps recently-viewed data hot if the user clicks back.
 * - `retry: false` because our 4xx errors aren't worth retrying — they're
 *   user errors. Network errors already show a banner.
 * - `refetchOnWindowFocus: true` so coming back to the tab gets fresh data
 *   even if it's stale.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
      staleTime: 30_000,
      gcTime: 5 * 60_000,
    },
    mutations: {
      retry: false,
    },
  },
});

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("missing #root");
}

// Theme: respect saved preference, fall back to OS
const stored = localStorage.getItem("lcloud-theme");
const wantsDark =
  stored === "dark" ||
  (stored === null && window.matchMedia("(prefers-color-scheme: dark)").matches);
if (wantsDark) {
  document.documentElement.classList.add("dark");
}

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);

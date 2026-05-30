import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      refetchOnWindowFocus: false,
      staleTime: 5_000,
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

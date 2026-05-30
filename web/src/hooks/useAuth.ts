import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { auth } from "@/api/client";
import type { AuthState, ThumbSize } from "@/api/types";

export interface AuthHookValue {
  data: AuthState | undefined;
  isLoading: boolean;
  isAdmin: boolean;
  refresh: () => Promise<unknown>;
}

export function useAuth(): AuthHookValue {
  const q = useQuery({
    queryKey: ["auth", "state"],
    queryFn: () => auth.state(),
    refetchInterval: (query) =>
      query.state.data?.authorized ? false : 4000,
  });
  return {
    data: q.data,
    isLoading: q.isLoading,
    isAdmin: q.data?.authorized === true,
    refresh: () => q.refetch(),
  };
}

/** Apply / toggle dark theme; returns current state + setter. */
export function useTheme(): [boolean, (v: boolean) => void] {
  const [dark, setDark] = useState<boolean>(() =>
    document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    localStorage.setItem("lcloud-theme", dark ? "dark" : "light");
  }, [dark]);
  return [dark, setDark];
}

/** Compression preference: re-encode images at upload (default), or upload byte-for-byte. */
export function useCompressUploads(): [boolean, (v: boolean) => void] {
  const [v, setV] = useState<boolean>(() => {
    const stored = localStorage.getItem("lc-compress-uploads");
    // Default: compression ON (saves storage)
    return stored === null ? true : stored === "true";
  });
  useEffect(() => {
    localStorage.setItem("lc-compress-uploads", String(v));
  }, [v]);
  return [v, setV];
}

const QUALITY_KEY_IMAGE = "lc-quality-image";
const QUALITY_KEY_VIDEO = "lc-quality-video";
const VALID_QUALITIES: ThumbSize[] = ["low", "med", "high"];

function _readQuality(key: string, fallback: ThumbSize): ThumbSize {
  const stored = localStorage.getItem(key) as ThumbSize | null;
  return stored && VALID_QUALITIES.includes(stored) ? stored : fallback;
}

/** Per-mediakind quality preference, persisted in localStorage. */
export function useImageQuality(): [ThumbSize, (q: ThumbSize) => void] {
  const [q, setQ] = useState<ThumbSize>(() => _readQuality(QUALITY_KEY_IMAGE, "low"));
  useEffect(() => {
    localStorage.setItem(QUALITY_KEY_IMAGE, q);
  }, [q]);
  return [q, setQ];
}

export function useVideoQuality(): [ThumbSize, (q: ThumbSize) => void] {
  const [q, setQ] = useState<ThumbSize>(() => _readQuality(QUALITY_KEY_VIDEO, "low"));
  useEffect(() => {
    localStorage.setItem(QUALITY_KEY_VIDEO, q);
  }, [q]);
  return [q, setQ];
}

/** Backward-compat single accessor; kept so existing code doesn't break. */
export function useQualityPreference(): [ThumbSize, (q: ThumbSize) => void] {
  return useImageQuality();
}

/** Re-renders when the viewport crosses the md breakpoint (768px). */
export function useIsMobile(): boolean {
  const [m, setM] = useState<boolean>(() => {
    if (typeof window === "undefined") return false;
    return window.matchMedia("(max-width: 767px)").matches;
  });
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const handler = (e: MediaQueryListEvent) => setM(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  return m;
}

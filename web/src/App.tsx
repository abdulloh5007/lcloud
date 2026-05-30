import { useState } from "react";
import { LoginScreen } from "./components/LoginScreen";
import { Sidebar } from "./components/Sidebar";
import { FilesPanel } from "./components/FilesPanel";
import { SettingsModal } from "./components/SettingsModal";
import {
  useAuth,
  useImageQuality,
  useTheme,
  useVideoQuality,
} from "./hooks/useAuth";
import { useAuthV2 } from "./hooks/useAuthV2";
import { auth } from "./api/client";
import { Button } from "./components/ui/Button";
import { Moon, Sun, LogOut, Menu } from "lucide-react";

export function App() {
  const { data, isLoading, refresh } = useAuth();
  const v2 = useAuthV2();
  const [selectedCloud, setSelectedCloud] = useState<number | null>(null);
  const [dark, setDark] = useTheme();
  const [imageQuality, setImageQuality] = useImageQuality();
  const [videoQuality, setVideoQuality] = useVideoQuality();
  const [mobileSidebar, setMobileSidebar] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  if (isLoading || v2.isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center text-sm text-neutral-500">
        …
      </div>
    );
  }

  // Sign-in gate: V2 cookie session is the source of truth for "logged in".
  // V1 (auth/state) tells us whether the userbot itself is connected to TG.
  if (!v2.isAuthenticated) {
    return (
      <LoginScreen
        bootstrapMode={data?.bootstrap_mode ?? true}
        userbotAuthed={data?.userbot_authed ?? false}
        onSignedIn={(kp) => {
          v2.setKeypair(kp);
          void v2.refresh();
        }}
        onAdminConnected={() => void refresh()}
      />
    );
  }

  return (
    <div className="h-[100dvh] flex flex-col">
      <header className="flex items-center justify-between px-3 sm:px-4 py-2 border-b border-neutral-200 dark:border-neutral-800 bg-panel dark:bg-panel-dark text-sm gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <button
            type="button"
            className="md:hidden p-2 -ml-2 text-neutral-600 dark:text-neutral-300"
            onClick={() => setMobileSidebar(true)}
            aria-label="Open sidebar"
          >
            <Menu size={20} />
          </button>
          {/* Identity is intentionally hidden — admin panel is identity-agnostic. */}
          <span className="inline-flex items-center gap-1.5 text-xs text-neutral-500">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
            connected
          </span>
          {!data?.userbot_started && (
            <span className="text-xs px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 hidden sm:inline">
              userbot offline
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 sm:gap-2 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setDark(!dark)}
            aria-label="Toggle theme"
          >
            {dark ? <Sun size={14} /> : <Moon size={14} />}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={async () => {
              await v2.logout();
              await auth.logout();
              void refresh();
            }}
          >
            <LogOut size={14} />
            <span className="hidden sm:inline">Выйти</span>
          </Button>
        </div>
      </header>
      <div className="flex-1 flex min-h-0">
        <Sidebar
          selectedCloudId={selectedCloud}
          onSelect={setSelectedCloud}
          mobileOpen={mobileSidebar}
          onMobileClose={() => setMobileSidebar(false)}
          onOpenSettings={() => setSettingsOpen(true)}
        />
        <FilesPanel cloudId={selectedCloud} quality={imageQuality} />
      </div>
      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        imageQuality={imageQuality}
        videoQuality={videoQuality}
        onChangeImageQuality={setImageQuality}
        onChangeVideoQuality={setVideoQuality}
      />
    </div>
  );
}

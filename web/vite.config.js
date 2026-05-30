import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
const API_PREFIXES = ["/auth", "/clouds", "/files", "/tags", "/search", "/health"];
const proxyTargets = Object.fromEntries(API_PREFIXES.map((p) => [
    p,
    {
        target: "http://127.0.0.1:8787",
        changeOrigin: false,
        cookieDomainRewrite: { "*": "" },
    },
]));
// https://vitejs.dev/config/
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "src"),
        },
    },
    server: {
        host: "127.0.0.1",
        port: 8788,
        strictPort: true,
        proxy: proxyTargets,
    },
    build: {
        outDir: "dist",
        sourcemap: false,
        chunkSizeWarningLimit: 1024,
    },
});

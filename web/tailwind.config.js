/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // Slate-based neutral palette; tag colours come from data.
        bg: {
          DEFAULT: "#fafafa",
          dark: "#0a0a0a",
        },
        panel: {
          DEFAULT: "#ffffff",
          dark: "#171717",
        },
      },
      fontFamily: {
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Inter",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};

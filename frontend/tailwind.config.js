/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      fontFamily: {
        display: ["Audiowide", "Orbitron", "sans-serif"],
        sans: ["Inter", "ui-sans-serif", "system-ui"],
        pixel: ["Inter", "ui-sans-serif", "system-ui"],
        holo: ["Orbitron", "ui-sans-serif", "system-ui"],
      },
      colors: {
        navy: {
          50: "#EEF1F8",
          100: "#D6DCEE",
          400: "#3B4C82",
          600: "#1F2E5C",
          700: "#182448",
          900: "#0E1730",
        },
        gold: {
          100: "#F7ECC8",
          400: "#D8B34A",
          500: "#C9A227",
          600: "#A6811A",
        },
        ink: "#1B1D26",
        holo: { 50: "#eafcff", 200: "#9ff2ff", 400: "#4bd5ee", 600: "#0891b2", 900: "#04222b" },
        crawl: { 400: "#fcd000", 500: "#e6b800" },
        rebel: { 500: "#3b82f6" },
        empire: { 500: "#ff3b3b", 700: "#7f1d1d" },
        void: "#05070d",
      },
      boxShadow: {
        soft: "0 1px 2px rgba(14,23,48,0.04), 0 8px 24px -8px rgba(14,23,48,0.12)",
        lift: "0 12px 32px -12px rgba(14,23,48,0.28)",
        glow: "0 0 0 4px rgba(201,162,39,0.15)",
      },
      keyframes: {
        fadeInUp: {
          "0%": { opacity: 0, transform: "translateY(10px)" },
          "100%": { opacity: 1, transform: "translateY(0)" },
        },
        popIn: {
          "0%": { opacity: 0, transform: "scale(0.96)" },
          "100%": { opacity: 1, transform: "scale(1)" },
        },
        pulseGlow: {
          "0%, 100%": { boxShadow: "0 0 0 0 rgba(201,162,39,0.45)" },
          "50%": { boxShadow: "0 0 0 8px rgba(201,162,39,0)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-400px 0" },
          "100%": { backgroundPosition: "400px 0" },
        },
        blob: {
          "0%, 100%": { transform: "translate(0,0) scale(1)" },
          "33%": { transform: "translate(20px,-30px) scale(1.08)" },
          "66%": { transform: "translate(-15px,15px) scale(0.96)" },
        },
        crawl: { "0%": { top: "100%" }, "100%": { top: "-50%" } },
        twinkle: { "0%, 100%": { opacity: "0.2" }, "50%": { opacity: "1" } },
        glowPulse: { "0%, 100%": { boxShadow: "0 0 5px rgba(75,213,238,.3)" }, "50%": { boxShadow: "0 0 24px rgba(75,213,238,.85)" } },
        flicker: { "0%, 100%": { opacity: "1", transform: "none" }, "42%": { opacity: ".55" }, "44%": { opacity: "1", transform: "skewX(-1deg)" }, "70%": { opacity: ".7" }, "71%": { opacity: "1" } },
        pixelPop: { "0%": { opacity: "0", transform: "scale(.92)" }, "100%": { opacity: "1", transform: "scale(1)" } },
      },
      animation: {
        fadeInUp: "fadeInUp 0.5s ease-out both",
        popIn: "popIn 0.35s cubic-bezier(0.34,1.56,0.64,1) both",
        pulseGlow: "pulseGlow 1.8s ease-in-out infinite",
        shimmer: "shimmer 1.6s linear infinite",
        blob: "blob 14s ease-in-out infinite",
        crawl: "crawl 3s linear forwards",
        twinkle: "twinkle 3s ease-in-out infinite",
        glowPulse: "glowPulse 1.5s ease-in-out infinite",
        flicker: "flicker 1.2s linear both",
        pixelPop: "pixelPop .28s steps(4, end) both",
      },
    },
  },
  plugins: [],
};

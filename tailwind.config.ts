import type { Config } from "tailwindcss";

export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#07110f",
        panel: "#0b1815",
        line: "#1b302a",
        mint: "#75f0c1",
        lime: "#d6f36d",
        fog: "#9db4ac"
      },
      boxShadow: { glow: "0 0 40px rgba(117,240,193,.12)", panel: "0 24px 70px rgba(0,0,0,.28)" },
      animation: { "fade-up": "fade-up .35s ease-out both", pulseSoft: "pulse-soft 2.8s ease-in-out infinite" },
      keyframes: {
        "fade-up": { from: { opacity: "0", transform: "translateY(8px)" }, to: { opacity: "1", transform: "translateY(0)" } },
        "pulse-soft": { "0%,100%": { opacity: ".55" }, "50%": { opacity: "1" } }
      }
    }
  },
  plugins: []
} satisfies Config;


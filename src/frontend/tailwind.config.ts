import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 殿阁朱墨卷轴风 - Ancient Chinese Scroll Style
        bg: {
          DEFAULT: "#0a0a0a", // 墨黑 - Ink Black
          panel: "#141414", // 面板深色
          surface: "#1a1a1a", // 表面
          highlight: "#252525", // 高亮
          secondary: "#0f0f0f", // 次级
          tertiary: "#1c1c1c", // 三级
        },
        border: {
          DEFAULT: "rgba(180, 160, 140, 0.15)", // 边框 - 古风米色调
          glow: "rgba(200, 80, 60, 0.3)", // 朱砂红 glow
        },
        accent: {
          DEFAULT: "#c85040", // 朱砂红 - Cinnabar Red (Primary)
          hover: "#d46858", // 浅朱砂
          secondary: "#4a9e9e", // 青玉 - Jade Cyan
          pink: "#b86e50", // 赭石
          dim: "rgba(200, 80, 60, 0.1)",
          text: "#e8d0c8", // 淡米色
        },
        status: {
          success: "#5a8a6a", // 青玉绿
          warning: "#c48840", // 鎏金橙
          error: "#c85040", // 朱砂红
          info: "#4a9e9e", // 青玉
          secondary: "#8a6a5a", // 赭石
        },
        text: {
          main: "#e8e4e0", // 宣纸白 - Rice Paper White
          muted: "#8a8580", // 淡墨
          dim: "#5a5550", // 暗墨
        },
        gold: {
          DEFAULT: "#c4a35a", // 鎏金
          light: "#d4b87a",
          dark: "#a48540",
        }
      },
      fontFamily: {
        sans: ['"Noto Sans SC"', '"Microsoft YaHei"', "ui-sans-serif", "system-ui", "sans-serif"],
        heading: ['"Noto Serif SC"', '"SimSun"', "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"Fira Code"', "ui-monospace", "monospace"],
      },
      backgroundImage: {
        'gradient-primary': 'linear-gradient(135deg, #c85040 0%, #b86e50 100%)',
        'gradient-surface': 'linear-gradient(to bottom right, rgba(200, 80, 60, 0.05), rgba(74, 158, 158, 0.05))',
        'gradient-gold': 'linear-gradient(135deg, #c4a35a 0%, #d4b87a 100%)',
      },
      animation: {
        "pulse-slow": "pulse 6s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "fade-in": "fadeIn 0.4s ease-out",
        "slide-in": "slideIn 0.3s ease-out",
      },
      keyframes: {
        fadeIn: {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        slideIn: {
          "0%": { transform: "translateY(10px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
      },
      boxShadow: {
        "glow": "0 0 15px rgba(200, 80, 60, 0.2)",
        "glow-lg": "0 0 30px rgba(200, 80, 60, 0.3), 0 0 10px rgba(196, 163, 90, 0.2)",
        "panel": "0 4px 20px 0 rgba(0, 0, 0, 0.4)",
        "scroll": "0 2px 8px rgba(0, 0, 0, 0.3)",
      }
    },
  },
  plugins: [],
} satisfies Config;

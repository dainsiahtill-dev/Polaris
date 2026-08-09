export default {
    content: ["./index.html", "./src/**/*.{ts,tsx}"],
    theme: {
        extend: {
            colors: {
                // Polaris game HUD brand system: void glass, tactical metal, precise cyan focus.
                bg: {
                    DEFAULT: "#070b14",
                    panel: "rgba(9, 18, 32, 0.88)",
                    surface: "#0d1a2b",
                    highlight: "#132744",
                    secondary: "#102033",
                    tertiary: "#17375c",
                },
                border: {
                    DEFAULT: "rgba(90, 217, 255, 0.24)",
                    glow: "rgba(0, 216, 255, 0.44)",
                },
                accent: {
                    DEFAULT: "#00d8ff",
                    hover: "#48e8ff",
                    secondary: "#80a6b4",
                    pink: "#ff4d6d",
                    dim: "rgba(0, 216, 255, 0.16)",
                    text: "#c7f7ff",
                },
                status: {
                    success: "#62ffcf",
                    warning: "#ffb848",
                    error: "#ff4d6d",
                    info: "#00d8ff",
                    secondary: "#80a6b4",
                },
                text: {
                    main: "#eafcff",
                    muted: "#b7dbe7",
                    dim: "#80a6b4",
                },
                gold: {
                    DEFAULT: "#ffb848",
                    light: "#ffd786",
                    dark: "#9b681b",
                }
            },
            fontFamily: {
                sans: [
                    "ui-sans-serif",
                    "system-ui",
                    "-apple-system",
                    "BlinkMacSystemFont",
                    '"SF Pro Text"',
                    '"Segoe UI"',
                    '"Noto Sans SC"',
                    '"Microsoft YaHei"',
                    "sans-serif",
                ],
                heading: [
                    "ui-sans-serif",
                    "system-ui",
                    "-apple-system",
                    "BlinkMacSystemFont",
                    '"SF Pro Display"',
                    '"Segoe UI"',
                    '"Noto Sans SC"',
                    '"Microsoft YaHei"',
                    "sans-serif",
                ],
                mono: ['"SF Mono"', '"JetBrains Mono"', '"Fira Code"', "ui-monospace", "monospace"],
            },
            backgroundImage: {
                'gradient-primary': 'linear-gradient(135deg, #00d8ff 0%, #62ffcf 100%)',
                'gradient-surface': 'linear-gradient(to bottom right, rgba(18, 42, 70, 0.92), rgba(6, 14, 27, 0.86))',
                'gradient-gold': 'linear-gradient(135deg, #ffb848 0%, #ffd786 100%)',
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
                "glow": "0 0 28px rgba(0, 216, 255, 0.24)",
                "glow-lg": "0 0 64px rgba(0, 216, 255, 0.28), 0 10px 30px rgba(255, 184, 72, 0.12)",
                "panel": "0 28px 80px rgba(0, 0, 0, 0.58), 0 0 44px rgba(0, 216, 255, 0.10)",
                "scroll": "0 3px 12px rgba(0, 216, 255, 0.22)",
            }
        },
    },
    plugins: [],
};

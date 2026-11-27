/** @type {import('tailwindcss').Config} */
// 绒花墨坊 UI 基座（2026-11-27，shadcn-vue 迁移第 1 步）
// 只做“接线 + 主题桥”：preflight 关掉，保证现有 3k 行 style.css 视觉不变。
module.exports = {
  content: ["./index.html", "./src/**/*.{vue,js}"],
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        ink: "var(--ink)",
        muted: "var(--muted)",
        accent: "var(--accent)",
        "accent-strong": "var(--accent-strong)",
        card: "var(--card)",
        border: "var(--border)",
        ok: "var(--ok)",
        warn: "var(--warn)",
        bad: "var(--bad)",
        field: "var(--field)",
        "field-hover": "var(--field-hover)",
        "field-inset": "var(--field-inset)",
      },
      borderRadius: {
        sm: "var(--r-sm)",
        md: "var(--r-md)",
        lg: "var(--r-lg)",
      },
      fontSize: {
        cap: "var(--fs-cap)",
        ui: "var(--fs-ui)",
        body: "var(--fs-body)",
        h3: "var(--fs-h3)",
        h2: "var(--fs-h2)",
      },
    },
  },
  plugins: [],
};

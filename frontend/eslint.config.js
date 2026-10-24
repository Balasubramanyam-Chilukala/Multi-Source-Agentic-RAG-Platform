import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "coverage", "playwright-report", "test-results"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended, jsxA11y.flatConfigs.recommended],
    languageOptions: { ecmaVersion: 2023, globals: globals.browser },
    plugins: { "react-hooks": reactHooks, "react-refresh": reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // Radix controls (Switch, Checkbox) render <button>s, which are labelable.
      "jsx-a11y/label-has-associated-control": ["error", { controlComponents: ["Switch", "Checkbox", "Input", "Select", "Textarea"], depth: 3 }],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
  {
    files: ["e2e/**/*.ts", "*.config.ts"],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
  },
  {
    // Providers, router and modules that intentionally co-locate helpers with components.
    files: ["src/components/**/*.tsx", "src/lib/**", "src/main.tsx", "src/router.tsx", "src/test/**", "**/*.test.{ts,tsx}", "src/features/**/*.tsx"],
    rules: { "react-refresh/only-export-components": "off" },
  },
);

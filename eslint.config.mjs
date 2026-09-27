import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "dist-electron/**",
    ".tmp/**",
    ".pnpm-store/**",
    ".understand-anything/**",
    "learn-claude-code/**",
    "release/**",
    // 运行期数据目录：部署产物 / 下载的 runtime bundle，永远不是 lint 目标。
    ".agenthub-data/**",
    // Python virtualenv（内部含第三方 .js 产物）与 vendored skill 脚本。
    "backend/.venv/**",
    "backend/.venv-ci/**",
    ".claude/skills/**",
    "apps/mobile/android/**/build/**",
    "apps/mobile/android/app/src/main/assets/**",
    "apps/mobile/dist/**",
    "apps/mobile/ios/App/App/public/**",
    "next-env.d.ts",
  ]),
  {
    rules: {
      "react-hooks/set-state-in-effect": "off",
    },
  },
]);

export default eslintConfig;

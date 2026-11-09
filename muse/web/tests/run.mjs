// Bundles the TypeScript tests with esbuild and runs them with node's test runner.
import { build } from "esbuild";
import { execFileSync } from "node:child_process";
await build({ entryPoints: ["tests/period.test.ts"], bundle: true, platform: "node", format: "esm",
              outfile: "tests/.out/period.test.mjs", logLevel: "error" });
execFileSync(process.execPath, ["--test", "tests/.out/period.test.mjs"], { stdio: "inherit" });

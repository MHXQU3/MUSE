// Builds demo/dist/index.html: one self-contained page (code, styles and
// gzipped data inline). Run `python demo/export_data.py ...` first.
import { build } from "esbuild";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { gzipSync } from "node:zlib";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const web = resolve(here, "..");

const shimPlugin = {
  name: "preview-shims",
  setup(b) {
    b.onResolve({ filter: /^next\/link$/ }, () => ({ path: resolve(here, "shims/link.tsx") }));
    b.onResolve({ filter: /^@\/lib\/routes$/ }, () => ({ path: resolve(here, "shims/routes.ts") }));
  },
};

const result = await build({
  entryPoints: [resolve(here, "main.tsx")],
  bundle: true, minify: true, write: false, format: "iife", target: "es2020",
  jsx: "automatic", tsconfig: resolve(web, "tsconfig.json"),
  nodePaths: (process.env.NODE_PATH || "").split(":").filter(Boolean),
  define: { "process.env.NODE_ENV": '"production"' },
  plugins: [shimPlugin], logLevel: "warning",
});
const js = result.outputFiles[0].text;
const css = readFileSync(resolve(web, "app/globals.css"), "utf8");
const data = gzipSync(readFileSync(resolve(here, "data.json")), { level: 9 }).toString("base64");

const html = `<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#1E1621">
<title>MUSE Timeline preview</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:ital,opsz,wght@0,6..96,400..600;1,6..96,400&family=Manrope:wght@400;500;600&display=swap" rel="stylesheet">
<style>${css}
.preview-note { width: var(--page); margin: 0.75rem auto 0; font-size: 0.8125rem; color: var(--paper-faint); }
</style>
</head>
<body>
<div id="root"><main class="timeline"><p class="loading" role="status">Opening your shelf…</p></main></div>
<script type="application/octet-stream" id="muse-data">${data}</script>
<script>${js.replace(/<\/script/gi, "<\\/script")}</script>
</body>
</html>`;
mkdirSync(resolve(here, "dist"), { recursive: true });
writeFileSync(resolve(here, "dist/index.html"), html);
console.log(`demo/dist/index.html: ${(html.length / 1e6).toFixed(2)} MB (data ${(data.length / 1e6).toFixed(2)} MB)`);

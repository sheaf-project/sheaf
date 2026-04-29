#!/usr/bin/env node
// Emits dist/build-manifest.json — a sha384 of every file in dist/, plus
// build provenance from VITE_* envs. Lets verifiers compare a deployed
// instance's served bundle against the manifest published by CI.
//
// Run after `vite build`.

import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { relative, resolve, sep } from "node:path";

const distDir = resolve(process.cwd(), "dist");
const manifestPath = resolve(distDir, "build-manifest.json");

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = resolve(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      out.push(...walk(full));
    } else if (st.isFile()) {
      out.push(full);
    }
  }
  return out;
}

function sri(buf) {
  return "sha384-" + createHash("sha384").update(buf).digest("base64");
}

const files = walk(distDir)
  .filter((p) => p !== manifestPath)
  .map((p) => {
    const buf = readFileSync(p);
    const rel = relative(distDir, p).split(sep).join("/");
    return { path: rel, size: buf.length, integrity: sri(buf) };
  })
  .sort((a, b) => a.path.localeCompare(b.path));

const manifest = {
  version: 1,
  git_commit: process.env.VITE_GIT_COMMIT || "",
  git_tag: process.env.VITE_GIT_TAG || "",
  build_time: process.env.VITE_BUILD_TIME || "",
  files,
};

writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n");
console.log(
  `wrote ${manifestPath} — ${files.length} files, ${
    manifest.git_tag || manifest.git_commit?.slice(0, 7) || "dev"
  }`,
);

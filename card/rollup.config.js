import { readFileSync } from "node:fs";

import resolve from "@rollup/plugin-node-resolve";
import typescript from "@rollup/plugin-typescript";
import commonjs from "@rollup/plugin-commonjs";
import terser from "@rollup/plugin-terser";
import { defineConfig } from "rollup";

import { nextBuild } from "./scripts/build-number.mjs";

// The version the card writes to the browser console, taken from package.json
// at build time. Hard-coding it in the source is how it drifted to 1.0.0 while
// the package was three releases further on — and package.json is the one copy
// the release workflow already checks against the tag, so this cannot go stale.
// Declared for TypeScript in src/build-globals.d.ts.
const { version } = JSON.parse(readFileSync("./package.json", "utf8"));

// Only `builddeploy.sh` asks for a build counter (CROWDSEC_BUILD_COUNTER);
// every other build, the GitHub release included, reports the bare semver.
// Bumped once per rollup run — a `watch` session keeps the number it started
// with, exactly like the build it stands in for.
const { build, builtAt, full } = nextBuild(version);
console.log(`crowdsec-bans-card ${full} (${builtAt})`);

// The target is the integration's www directory directly: the integration
// serves the card itself, so no Lovelace resource has to be maintained by
// hand.
export default defineConfig({
  input: "src/crowdsec-bans-card.ts",
  output: {
    file: "../custom_components/crowdsec/www/crowdsec-bans-card.js",
    format: "es",
    sourcemap: true,
    intro: [
      `const CARD_VERSION = ${JSON.stringify(full)};`,
      `const CARD_SEMVER = ${JSON.stringify(version)};`,
      `const CARD_BUILD = ${JSON.stringify(build)};`,
      `const CARD_BUILD_TIME = ${JSON.stringify(builtAt)};`,
    ].join("\n"),
  },
  plugins: [
    resolve({ browser: true, preferBuiltins: false }),
    commonjs(),
    typescript({ tsconfig: "./tsconfig.json", declaration: false }),
    terser({ format: { comments: false } }),
  ],
  context: "window",
});

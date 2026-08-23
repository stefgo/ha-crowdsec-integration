/**
 * Local build counter for the version the card reports.
 *
 * The number rises with every local deploy build, so a reloaded dashboard shows
 * one nobody has seen before — if the console still prints the old one, the
 * browser served a cached bundle rather than the freshly deployed one. That is
 * the whole point of it; the released version stays the semver in
 * `package.json`, which is what the release workflow checks against the tag.
 *
 * The counter is local to the working copy (`.build-number` is not committed):
 * it answers "is this the bundle I just built?", not "which build is this
 * across machines". It is therefore opt-in — only `builddeploy.sh` asks for it
 * (`CROWDSEC_BUILD_COUNTER=1`). A plain `npm run build`, and with it the
 * release workflow on GitHub, reports the bare semver.
 */
import { existsSync, readFileSync, writeFileSync } from "node:fs";

const BUILD_NUMBER_FILE = ".build-number";

/** True when this build should carry a local build counter */
export function wantsBuildNumber() {
  const flag = process.env.CROWDSEC_BUILD_COUNTER;
  return flag !== undefined && flag !== "" && flag !== "0";
}

/**
 * Returns the version parts for this build, bumping the counter when one was
 * asked for. Without it `build` is 0 and `full` is the plain semver.
 */
export function nextBuild(version) {
  const builtAt = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

  if (!wantsBuildNumber()) {
    return { build: 0, builtAt, full: version };
  }

  const previous = existsSync(BUILD_NUMBER_FILE)
    ? Number.parseInt(readFileSync(BUILD_NUMBER_FILE, "utf8").trim(), 10)
    : 0;
  const build = Number.isFinite(previous) && previous > 0 ? previous + 1 : 1;

  writeFileSync(BUILD_NUMBER_FILE, `${build}\n`);

  return { build, builtAt, full: `${version}+build.${build}` };
}

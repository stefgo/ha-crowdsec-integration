/**
 * Values the build injects into the bundle.
 *
 * All four are defined by rollup's `output.intro` (see `rollup.config.js`), so
 * the numbers the card reports are the built ones rather than constants
 * somebody has to remember to bump.
 *
 * `CARD_SEMVER` is the released version from package.json. `CARD_BUILD` is the
 * local build counter (`scripts/build-number.mjs`): it rises with every
 * `builddeploy.sh` build, so a cached bundle is recognisable by its number, and
 * is 0 in a release build. `CARD_VERSION` is what the card prints —
 * `<semver>`, or `<semver>+build.<n>` for a local deploy build.
 */
declare const CARD_VERSION: string;
declare const CARD_SEMVER: string;
declare const CARD_BUILD: number;
declare const CARD_BUILD_TIME: string;

// Flat config. The card is checked, not styled: formatting is left alone, and
// the type system is tsc's job (`npm run typecheck`) — so this stays on the
// syntactic rules, which is also why it needs no type information and runs in
// a second.
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/", "node_modules/"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    // The build tooling runs in Node, the card in the browser.
    files: ["rollup.config.js", "eslint.config.js", "scripts/**"],
    languageOptions: { globals: globals.node },
  },
  {
    files: ["src/**", "test/**"],
    languageOptions: { globals: globals.browser },
  },
  {
    rules: {
      // The card talks to Home Assistant's untyped frontend internals; those
      // accesses are guarded at the call site, which is what `any` is doing
      // there. Flagging every one of them would drown the real findings.
      "@typescript-eslint/no-explicit-any": "off",
      // A leading underscore is the local convention for "deliberately
      // unused" — most of them are caught errors that are ignored on purpose.
      "@typescript-eslint/no-unused-vars": [
        "error",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
          // `const { start, end, ...rest } = x` is how a key is dropped from
          // an object here; the named siblings are the point of it.
          ignoreRestSiblings: true,
        },
      ],
    },
  }
);

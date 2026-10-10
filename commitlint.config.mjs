export default {
    extends: ["@commitlint/config-conventional"],
    // The commit the release workflow creates carries the release notes as its
    // body, with lines far longer than body-max-line-length allows.
    ignores: [(message) => /^chore\(release\): \d+\.\d+\.\d+/.test(message)],
    plugins: [
        {
            rules: {
                // Conventional Commits allows two spellings of a breaking change:
                // `feat!:` and the `BREAKING CHANGE:` footer. Only the footer is
                // allowed here.
                //
                // The release workflow reads commits with the Angular preset, whose
                // headerPattern is /^(\w*)(?:\((.*)\))?: (.*)$/ -- without `!`. A
                // `feat!: ...` falls through it, is read as typeless and releases
                // nothing, while commitlint's own parser would accept it.
                //
                // Second reason: a breaking change raises the minor position here
                // (breaking: minor in release.yml). A `!` tells every reader
                // "major" and would claim something false.
                "no-breaking-bang": ({ header }) => [
                    !/^[a-z]+(\([^)]*\))?!:/.test(header ?? ""),
                    'The "!" is not used here. Use a "BREAKING CHANGE:" footer instead.',
                ],
            },
        },
    ],
    rules: {
        // Commit messages are English. The default rule forbids sentence-case
        // and would reject the natural form of an English subject
        // ("fix: Validate the settings before saving them"). The type is what
        // matters for a release, not the capitalisation behind it.
        "subject-case": [0],

        "no-breaking-bang": [2, "always"],
    },
};

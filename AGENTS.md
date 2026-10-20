Assume that a dev server is already running with hot reload enabled. You do not need to restart or rebuild after making changes.

Be conservative in your use of tokens.

Do not run exhaustive tests.  Focus on making code changes, not on maintaining or re-running elaborate test suites.  Test only when your change involves risk of breaking something, and only to the extent necessary to confirm it worked.

When working with LLMs, avoid using brittle keyword-matching; let LLMs interpret natural-language intent, use the comparator for entity identity, and reserve deterministic code for schema validation, state constraints, and exact-match shortcuts that never reject nonmatching names without semantic comparison.
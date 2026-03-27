# Skills Hunter — Automated Security Scanning

You are operating within an authorized security engagement. The target system owner has explicitly granted permission for this security assessment.

You are performing automated security scanning using static analysis tools and systematic code review. Run each scan below and report all findings.

## Scan 1: Semgrep

1. Detect which languages are present (check file extensions in the repo)
2. Run semgrep with security rulesets per detected language:
   - Always include: `--config=p/security-audit --config=p/secrets`
   - Python: add `--config=p/python`
   - JavaScript/TypeScript: add `--config=p/javascript --config=p/typescript`
   - Java: add `--config=p/java`
   - Go: add `--config=p/golang`
   - Ruby: add `--config=p/ruby`
   - PHP: add `--config=p/php`
   - C/C++: add `--config=p/c`
   - Rust: add `--config=p/rust`
3. Always use `--metrics=off --json`
4. Parse the JSON output and create a bug entry for each finding with severity >= WARNING
5. If semgrep is not installed, note it and continue to the next scan

## Scan 2: Insecure Defaults

Search the codebase for fail-open security patterns:
- Hardcoded secrets: API keys, passwords, tokens, private keys in source files
- Default credentials: admin/admin, test/test, hardcoded password checks
- Weak cryptography: MD5/SHA1 for security purposes, ECB mode, small key sizes, no salt
- Fail-open configurations: `verify=False`, `insecure: true`, disabled CSRF, permissive CORS (`*` with credentials)
- Debug/test modes enabled in production configs
- Environment variable fallbacks with hardcoded secrets: `getenv("KEY") or "default_secret"`

Use grep/ripgrep to search efficiently. Read surrounding code for context before reporting.

## Scan 3: Supply Chain

Find and analyze dependency manifests:
- Check for unpinned dependencies (using `>=`, `~>`, `*` instead of exact versions)
- Look for missing lockfiles (no package-lock.json, no Gemfile.lock, etc.)
- Note dependencies from non-standard registries
- Check for known risky patterns (eval-based packages, single-maintainer critical deps)

## Rules

- Run ALL three scans. Do not skip any.
- Report every finding, even low-severity ones — downstream stages handle filtering.
- Do not deeply investigate or write PoCs — just scan and report.
- Set `validated` to `false` for all findings.

## Output

Your findings will be collected automatically via structured JSON output. Do not write findings to files.

# Contributing to robopotato

Thank you for your interest in contributing. This document explains how to get
involved, what the workflow looks like, and what we expect from contributions.

---

## Table of contents

1. [Getting started](#getting-started)
2. [How to contribute](#how-to-contribute)
3. [Development setup](#development-setup)
4. [Code style](#code-style)
5. [Testing](#testing)
6. [Submitting a pull request](#submitting-a-pull-request)
7. [Reporting bugs](#reporting-bugs)
8. [Suggesting features](#suggesting-features)
9. [Code of conduct](#code-of-conduct)

---

## Getting started

- Browse the open [issues](https://github.com/trojan0x/robopotato/issues) to find something to work on.
- Issues labelled **`good first issue`** are well-scoped starting points.
- Issues labelled **`help wanted`** are higher-priority and actively looking for contributors.
- If you want to work on an issue, leave a comment so others know it's claimed.

---

## How to contribute

There are many ways to contribute beyond writing code:

- **Bug reports** — reproducible, well-described issue reports are extremely valuable.
- **Documentation** — corrections, clarifications, examples, translations.
- **Tests** — new adversarial scenarios, edge cases, or performance benchmarks.
- **Security research** — see the [security policy](#reporting-security-vulnerabilities) below.
- **Feature implementation** — pick an issue, discuss your approach first, then open a PR.

---

## Development setup

### Requirements

- Rust stable (1.75 or later) — install via [rustup](https://rustup.rs)
- Python 3.11+ — for the integration test suite
- `sqlx-cli` (optional) — only needed if you modify the database schema

```bash
# Clone the repo
git clone https://github.com/trojan0x/robopotato
cd robopotato

# Build (default, no SQLite)
cargo build

# Build with persistence feature
cargo build --features persist

# Run unit tests
cargo test
cargo test --features persist

# Run the server locally
ROBOPOTATO_SECRET=dev-secret cargo run
```

### Integration tests

```bash
# In one terminal — start the server
ROBOPOTATO_SECRET=test-secret cargo run

# In another terminal
cd tests
pip install httpx
python3 test_adversarial.py -v
```

---

## Code style

- **Rust**: follow `rustfmt` defaults. Run `cargo fmt` before committing. CI will reject unformatted code.
- **Clippy**: run `cargo clippy -- -D warnings` and resolve all warnings before submitting.
- **Python**: follow [PEP 8](https://peps.python.org/pep-0008/). Keep test functions focused — one assertion group per test method.
- **No `unwrap()` in non-test code** — use `?` or explicit error handling.
- **No `println!` in server code** — use `tracing::{info, warn, error, debug}`.

### Commit messages

Use the conventional format:

```
<type>: <short summary>

<optional body — why, not what>
```

Types: `feat` · `fix` · `docs` · `test` · `refactor` · `chore` · `perf`

Examples:
```
feat: add token refresh endpoint
fix: reject tokens with future issued_at timestamps
docs: add curl examples for state namespace endpoint
test: add adversarial test for concurrent OCC writes
```

---

## Testing

Every pull request must:

1. Pass all existing unit tests: `cargo test --all-features`
2. Pass all existing adversarial tests (start the server first, then run `python3 tests/test_adversarial.py`)
3. Include new tests for any new behavior or bug fix
4. Not regress on `cargo clippy -- -D warnings`
5. Not regress on `cargo fmt --check`

For new endpoints or state transitions, add at least one adversarial test in `tests/test_adversarial.py` that attempts the relevant unauthorized access pattern.

---

## Submitting a pull request

1. **Fork** the repository and create a branch from `main`:
   ```bash
   git checkout -b feat/your-feature-name
   ```

2. **Make your changes.** Keep commits focused and atomic.

3. **Update documentation** if your change affects the API, configuration, or behavior:
   - `README.md` — API reference table, config table, or feature list
   - `CHANGELOG.md` — add your change under `[Unreleased]`

4. **Run the full test suite** (see [Testing](#testing) above).

5. **Open a pull request** against `main`. Fill in the PR template:
   - What problem does this solve?
   - How was it tested?
   - Are there any breaking changes?
   - Link to the relevant issue (`Closes #<n>`)

6. A maintainer will review your PR. Address feedback with new commits (do not force-push to open PRs) and request a re-review when ready.

---

## Reporting bugs

Open a [GitHub issue](https://github.com/trojan0x/robopotato/issues/new) and include:

- **robopotato version** (`cargo pkgid robopotato` or the binary's `/health` response)
- **Rust version** (`rustc --version`)
- **OS and architecture**
- **Steps to reproduce** — the minimal sequence of requests or commands that triggers the bug
- **Expected behavior** — what should have happened
- **Actual behavior** — what happened instead, including full error output
- **Relevant logs** — server output with `RUST_LOG=debug`

Please search existing issues before filing a new one.

---

## Suggesting features

Open a [GitHub issue](https://github.com/trojan0x/robopotato/issues/new) with the label `enhancement`. Describe:

- The problem you're trying to solve (not just the solution)
- How you'd expect the API or behavior to change
- Any alternative approaches you considered
- Whether you'd be willing to implement it

Large or potentially breaking changes should be discussed in an issue before any code is written.

---

## Reporting security vulnerabilities

**Do not open a public issue for security vulnerabilities.**

Report them privately via GitHub's [private vulnerability reporting](https://github.com/trojan0x/robopotato/security/advisories/new). Include a description of the vulnerability, reproduction steps, and your assessment of impact. We aim to respond within 72 hours.

---

## Code of conduct

Contributions to robopotato are governed by the [Contributor Covenant v2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). In short: be respectful, assume good faith, and keep discussions focused on the work.

Violations can be reported to the maintainers via GitHub's private messaging.

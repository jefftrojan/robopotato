## What does this PR do?

<!-- 1-3 sentences describing the change. -->

Closes #<!-- issue number -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / cleanup
- [ ] Documentation
- [ ] Tests only
- [ ] Dependency update

## How was it tested?

<!-- Describe how you verified the change works and doesn't break anything. -->

- [ ] `cargo test --all-features` passes
- [ ] `cargo clippy -- -D warnings` passes
- [ ] `cargo fmt --check` passes
- [ ] Adversarial integration tests pass (`python3 tests/test_adversarial.py -v`)
- [ ] New tests added for new behavior

## Checklist

- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] `README.md` updated if API, config, or behavior changed
- [ ] No `unwrap()` calls added in non-test code
- [ ] No `println!` added in server code (use `tracing::info/warn/error`)
- [ ] Breaking changes documented (if any)

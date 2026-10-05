# Development policy

## Testing and gate

The full gate is the existing `test` job matrix in `.github/workflows/ci.yml`:
Linux x86_64/aarch64 and macOS arm64/x86_64. No reduced scopes are defined.
Required checks are locked dependency installation, architecture verification,
Ruff, functional/memory/ABI/packaging tests, full-frame timing, distribution
build, and clean-wheel installation and terminal smoke test. Follow the workflow's
exact commands and environment settings. Hosted Intel timing is report-only;
all other checks remain mandatory. The scheduled `abi-drift` job additionally
checks the latest upstream binding and must retain `--no-sync`.

Local validation executes the same applicable checks on the available native
platform. It cannot establish a four-platform green gate; report the platform
and any missing CI evidence explicitly. A release candidate requires all four
platform jobs to pass for its source revision. Existing CI release smoke checks
must use the dependency version specified by the project's exact pin.

The gate runner must be independent of the author, start from the supplied
candidate SHA and a clean tracked source tree, and run the full applicable gate
once without editing sources or reducing scope. Retain command output, exit
codes, platform, SHA and comparison base under a unique evidence directory.
The evidence directory is ignored so gate output does not dirty the candidate.
Report stages not executed as NOT RUN and finish with
`OVERALL: GREEN | RED | BLOCKED (<reason>)`. Do not treat historical evidence
or skipped required tests as a current pass.

Regression tests must demonstrate the intended behavioral failure before the
fix and pass after it. Use real bundled native libraries for ABI, rendering,
mode, scrollback and clipboard integration checks. Unit tests may additionally
exercise callback reply/error branches; they do not replace native coverage.

## Baselines

Baseline source for the ABI migration: `45c2aa1fdf410b970e83a5ef60cb3af4cd556322`.
It pins `pyghostty==0.1.1`. Native ABI and long-grapheme tests pass at that
baseline. Performance expectations and measurement instructions are owned by
`README.md`, `docs/performance.md`, and the CI workflow. Characterization changes
require an explicit operator ruling recorded with the change's spec.

## Documentation map

`README.md` owns installation, platform support, development and releases.
`docs/performance.md` owns performance results. Historical design documents are
under `docs/superpowers/`; new contracts, reviews and handoffs go in `.ayder/`.
Search affected symbols and dependency-version references with `rg`, including
README, source, tests, workflow, pyproject and lockfile. Do not rewrite historical
documents as if they describe the new implementation.

## Routing and ownership

The coordinator authors contracts and owns operator rulings. Implementation is
serial. Use a fresh Codex sub-agent with inherited model settings for independent
spec, plan and branch reviews; it may write its review artifact but not its
subject. Use a separate fresh gate runner for candidate verification. Record
artifact paths, baseline/candidate SHA and evidence paths in each handoff.
No parallel writers or shared test-estate runs are authorized.

There are currently no built-binary seeded-harness tests. Any introduced tests of
that kind are operator-written layers and must be developed with the operator.
Do not merge, tag or release from an agent. Opening a PR requires literal
`PR confirmed` under dev-contract-v2.

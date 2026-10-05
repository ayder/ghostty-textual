# pyghostty 0.1.3 — plan

**Tier:** T2 — as recorded in the approved spec.
**Spec:** [approved revision](../../specs/pyghostty-0.1.3.md)
**Baseline:** main, 45c2aa1fdf410b970e83a5ef60cb3af4cd556322, package 0.0.4 / binding 0.1.1.
**Evidence:** ../../evidence/pyghostty-0.1.3/

### Task K1: Core native ABI and dependency migration

**Depends:** none
**Create/modify:** pyproject.toml; uv.lock; src/ghostty_textual/_native.py REQUIRED_SYMBOLS; src/ghostty_textual/_render.py RenderState.update; src/ghostty_textual/emulator.py Terminal._create_native and _mode; tests/conftest.py make_terminal; tests/test_abi.py; tests/test_emulator.py; tests/test_packaging.py; .github/workflows/ci.yml wheel smoke and ABI drift output.
**Contract:** Consume the real pyghostty 0.1.3 FFI: construction takes dimensions, scrollback setters consume size_t*, colors use a sized GhosttyRenderStateColors*, and modes use an initialized GhosttyTerminalModeConfig*. Produce unchanged public Terminal and snapshot/modes interfaces. The exact dependency remains a distribution contract.
**Change:** Replace the removed APIs and options struct; apply scrollback inside construction's cleanup-protected block. Preserve scrollback=0 using SCROLLBACK_MAX_BYTES=0; positive scrollback uses SCROLLBACK_MAX_LINES and its existing page-granular retention. Retain the current false-on-unsuccessful-mode-query behavior. Remove the two obsolete required symbols; the generic getters/setter are already required. Update locked dependency and smoke expectations together and print the tested binding version in the scheduled job. No dual-version runtime adapter. AC1, AC2, AC3 and AC6.
D1: Query DEC private modes using the same integer encoding already consumed by this wrapper; read config.value only on success.
**Witnesses:** [K1 proof decisions](tests/K1.md)
**Review:** Exact distribution requirement, cleanup on scrollback setter failure, non-native import, correct input/output pointer types, and no implicit resynchronization of the scheduled upgraded environment.

### Task K2: Synchronous clipboard reply migration

**Depends:** K1
**Create/modify:** src/ghostty_textual/emulator.py Terminal._register_clipboard_callback; tests/test_security.py.
**Contract:** Consume void GhosttyTerminalClipboardWriteFn and borrowed sized request with synchronous reply function. Produce unchanged opt-in ClipboardWritten notifications and deferred callback exceptions, plus the native reply result required by AC4.
**Change:** Use a void callback, choose the existing success/denied/I/O-error result, and send one sized GhosttyClipboardWriteReply before returning. Do not retain the borrowed request. Keep remember false. Preserve first eligible text representation and existing per-representation size checks. Do not enable new clipboard protocols or register read callbacks in production.
D2: Never send a remembered grant or retain a clipboard request beyond its callback lifetime.
**Witnesses:** [K2 proof decisions](tests/K2.md)
**Review:** Actual C callback signature, reply on every normal/error branch, size initialization, retained callback lifetimes, and real native acknowledgements rather than notification-only coverage.

### Task K3: Authorized grapheme characterization and consumer documentation

**Depends:** K1, K2
**Create/modify:** tests/test_render_optimizations.py; README.md; tests/test_abi.py docstring; .ayder/ledger.md and evidence/review artifacts.
**Contract:** Consume the authorized upstream cap of 64 suffix codepoints; preserve all supported UTF-8 data and reusable buffer growth. Document the limitation without promising unlimited graphemes.
**Change:** Replace the unsupported 180-accent growth input with a base plus 64 U+1D185 codepoints. Add independent cap coverage, including fragmented input, and retain style and following-cell assertions. Update current consumer dependency references only; leave historical designs unchanged. AC5 and AC6.
**Witnesses:** [K3 proof decisions](tests/K3.md)
**Review:** Genuine 257-byte buffer growth, cap boundary and following-cell fidelity, explicitly authorized characterization, documentation and unchanged performance/CI contracts.

## Execution and delivery

Keep witness-test commits separate from subsequent fixing commits. K1's native red uses the actual 0.1.3 wheel against baseline source; preserve the behavioral initialization error. Preservation witnesses run against 0.1.1 before migration and 0.1.3 afterwards. K2's test commit follows K1, so its red is the missing native reply, not an earlier loader failure. Record the authorized cap red using baseline source and 0.1.1 in an isolated checkout with the cap witness added; do not call a cap failure a wrapper regression fix. Complete K3 before committing the final candidate. Run the independent full local gate and branch review on that candidate. Four-platform CI remains required before release and is reported missing if the candidate has not been pushed; no PR, tag or release publication is included.

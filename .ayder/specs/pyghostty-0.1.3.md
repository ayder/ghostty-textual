# pyghostty 0.1.3 migration

Tier: T2 — native ABI and callback migration with an explicitly authorized
terminal-characterization change. One shippable dependency migration.

## Intent and scope

Upgrade the exact pyghostty dependency from 0.1.1 to 0.1.3 so the current
upstream binding is usable and the scheduled ABI drift probe passes against
0.1.3. Adapt construction, render colors, mode queries and clipboard writes.
Keep public Python signatures, lazy native loading, clipboard opt-in and size
policy, scrollback behavior, terminal cleanup and rendering semantics intact,
except for the operator-authorized upstream grapheme cap.

No native fork, alternate emulator, dual-version adapter, release publication,
or change to performance budgets is included. The package version remains
0.0.4 during implementation; a subsequent release requires its own version
bump and matching tag after all required CI jobs pass.

## Checked premises

Baseline: `45c2aa1fdf410b970e83a5ef60cb3af4cd556322`, pyghostty 0.1.1.
Scheduled run 37302831339 upgraded to 0.1.3 and failed on two missing symbols.
The actual 0.1.3 wheel's mode and colors getters were successfully probed.
The versioned declaration comparison and exploratory native test output are
in [evidence](../evidence/pyghostty-0.1.3/). Exploratory results are not gate proof.

pyghostty 0.1.3 bundles Ghostty revision
`6467b1dab0be087fa8f0a7ccc7b3c5b88b9be7a5`.
Its colors getter uses the existing sized colors struct. Its mode getter uses
an input/output GhosttyTerminalModeConfig with mode initialized and value read
on success. Construction now takes dimensions directly; scrollback is a setter.
Clipboard callbacks return void and synchronously call the request's reply
function with a sized reply. Returning without a reply denies the request.

The native page implementation intentionally caps additional grapheme
codepoints at 64 to bound memory and avoid quadratic copying. The base
codepoint is separate. No public override is available.

## Operator rulings

The operator authorized the minimal repository policy based on existing CI.
The operator chose: adopt the upstream limit, retain a genuine UTF-8
buffer-growth test within it, and continue through reviews and implementation.
Preserving 180 combining accents via a patched native library is excluded.

## Acceptance criteria

AC1: Locked installation and built distributions require pyghostty exactly
0.1.3; the native loader succeeds and ABI checks pass against its real wheel.

AC2: Terminal construction honors dimensions and requested scrollback; native
cleanup remains safe, and construction/import error boundaries are preserved.

AC3: Render-state colors, palette updates and mode-dependent behavior retain
their existing observable results using the replacement generic getters.

AC4: Clipboard writes remain opt-in, text-only and bounded by ClipboardPolicy.
The 0.1.3 callback returns void and sends success, denial or I/O error through
a correctly sized synchronous reply; remember remains false. Notification
and exception handling preserve current behavior.

AC5: A styled cell with a base character plus 64 four-byte combining
codepoints preserves all 257 UTF-8 bytes, grows the initial 256-byte buffer,
and leaves the following cell and style intact. Inputs exceeding the upstream
cap retain the base plus the first 64 suffix codepoints and leave the following
cell intact. The limitation is documented for consumers.

AC6: The normal four-platform CI contract, lazy package import, wheel smoke,
functional/memory checks and performance budgets are preserved. The scheduled
job still upgrades upstream and tests without synchronizing back to the pin;
its output identifies the tested version. Validation reports distinguish local
evidence from the required four-platform CI gate.

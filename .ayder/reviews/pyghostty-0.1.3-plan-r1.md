Reviewed: `.ayder/plans/pyghostty-0.1.3/plan.md` and all linked witnesses, revision 1; baseline `45c2aa1fdf410b970e83a5ef60cb3af4cd556322`
Verdict: APPROVED
Bundle SHA256: 48ea3856a118981cc33a76d2b4e104b6e3474e53aaffbdd37cf06e377c445b61

Coverage:

| Criterion / decision | Witnesses |
|---|---|
| AC1 | [W1](tests/K1.md), [C1](tests/K1.md) |
| AC2 | [W2](tests/K1.md) |
| AC3 | [W3](tests/K1.md) |
| AC4 | [W5](tests/K2.md), [W6](tests/K2.md), [W7](tests/K2.md), [W8](tests/K2.md) |
| AC5 | [W9](tests/K3.md), [W10](tests/K3.md), [C3](tests/K3.md) |
| AC6 | [W4](tests/K1.md), [C2](tests/K1.md), [C3](tests/K3.md), [C4](tests/K3.md) |
| D1 | [W3](tests/K1.md) |
| D2 | [W7](tests/K2.md) |

Findings: None.

Checked:

- Read AGENTS.md, policy, ledger, approved spec and its independent review, all three witness files, and dev-contract-v2 plan/review references. HEAD matches the baseline; the policy/contract tree is untracked and .gitignore contains the policy-authorized evidence-output exclusion. No product or subject files were changed, and no shared tests were run.
- Ran `python3 /Users/sinanalyuruk/.codex/skills/dev-contract-v2/scripts/check-plan.py .ayder/plans/pyghostty-0.1.3/plan.md --table`; exit 0. Every AC and declared decision is covered. The table above is the checker-generated coverage output.
- Inspected existing loader, constructor, callback, feed, ABI, emulator, packaging and buffer-growth tests plus the full workflow. K1's paths and consumed types are concrete; applying the scrollback setter inside the cleanup block preserves the native ownership boundary. W2/W3/W4 cover observable retained behavior; construction cleanup remains an explicit review obligation, supported by the existing lifecycle suite and full gate.
- Opened retained baseline ABI red, supported-grapheme and preservation output, declaration comparison and exploratory functional result. Baseline source with the actual 0.1.3 wheel fails for the named missing symbols, making W1 reachable. Preservation output is historical planning evidence, not candidate gate proof.
- Inspected versioned `terminal.h` and `c-terminal.zig` in `/tmp/ghostty-api-investigation/`. The clipboard write callback returns void; request.reply accepts a sized reply synchronously; no reply means denial. The header maps success/denied/I/O-error to OSC 5522 DONE/EPERM/EIO. Upstream's complete begin/data/commit transaction establishes a reachable acknowledgement path. W5/W6 explicitly enable test-only spool capacity, so they avoid a disabled-spooling false red and do not expand production protocols.
- Confirmed feed appends PTY output before raising a deferred callback error; W6 can inspect the terminal's collected output after catching GhosttyError. W7 can invoke the retained CFFI callback with a fully sized native-layout request, live MIME/data buffers and capture-only reply function; it captures size/result/remember during the call rather than retaining borrowed pointers. Native W5/W6 supplement this unit fixture, and K2 runs after K1 so callback red is not obscured by loading failures.
- A read-only isolated wheel probe confirmed the actual callback type is void, GhosttyClipboardWriteReply is available and sized, and the clipboard spool setter constant exists. It also confirmed U+1D185 is MUSICAL SYMBOL COMBINING DOIT and encodes to four bytes. W9's base plus 64 suffixes is exactly 257 bytes and crosses the current 256-byte scratch buffer. Inspected page.zig's 64-suffix truncation and append guard. W10 explicitly distinguishes its authorized characterization red on baseline 0.1.1 from a wrapper regression; whole/fragmented inputs and following-cell/style checks are meaningful.
- C1/C2/C3/C4 settle exact distribution dependency, scheduled upgrade with --no-sync, consumer documentation and complete local gate. Four hosted platform jobs remain required and explicitly NOT RUN if unpublished. No reduced gate, budget change, dual-version adapter or release publication is introduced. Witness commits remain separate from fix commits, and user authorization permits proceeding after approved-plan handoff.

Remaining proof: Execute and retain the planned historical red/preservation pairs, candidate green, independent full local gate and branch review. Required four-platform CI remains outstanding until an authorized published source revision is checked. These are execution obligations, not unresolved plan decisions.

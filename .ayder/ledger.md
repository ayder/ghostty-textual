# Current handoff

Objective: migrate to pyghostty 0.1.3 with the authorized upstream grapheme cap.
Tier: T2; baseline 45c2aa1fdf410b970e83a5ef60cb3af4cd556322.
Spec: `.ayder/specs/pyghostty-0.1.3.md`, revision 1.
Policy: `AGENTS.md` and `.ayder/policy.md`, authorized by the operator.
Evidence: `.ayder/evidence/pyghostty-0.1.3/`.
Ruling: accept 64 suffix codepoints and retain a 257-byte buffer-growth witness;
continue through spec and plan reviews into implementation.
Spec review: `.ayder/reviews/pyghostty-0.1.3-spec-r1.md`, APPROVED.
Plan: `.ayder/plans/pyghostty-0.1.3/plan.md`, revision 1.
Current plan bundle SHA256: 541ad4c1dc87dc5adf98f45ed7d00a99d536ac6b0bae3cc1143983e941e29cb2.
Prior implementation snapshot: 3c4d9434d2ef74406d7fd2ff151a1b9217ef61cba9ba35b813d0f60578a3e048.
Plan review: `.ayder/reviews/pyghostty-0.1.3-plan-r2.md`, APPROVED; supersedes r1.
Status: approved spec and plan handoff closed; user authorized continuation.
Implementation branch: fix/pyghostty-0.1.3; coordinator implements K1–K3 serially.
Baseline SHA and approved digest revalidated before development.
Dispatches: spec_review completed; plan_review completed revision 2.
Release: no tag, release or PR publication authorized.

## Implementation handoff

K1 test commit: 0af6b2b; fix commit: 9191142.
K2 regression test commit: 954d2d1; fix commit: 1fc58e9.
K3 characterization/growth witness commit: 98e89ec; consumer docs reconciled.
Historical proof: baseline-0.1.3-abi-red.txt (1 failed, 5 passed, 5 errors);
baseline-preservation.txt (36 passed), k1-preservation-before.txt (3 passed),
k1-green.txt (34 passed), k2-red.txt (5 failed, 5 passed), k2-green.txt
(10 passed), k3-baseline-characterization.txt (2 authorized cap failures,
1 supported-input pass), k3-green.txt (9 passed).
Pre-candidate functional run: 139 passed, timing test intentionally separate.
Status: K1–K3 complete; final docs commit is the candidate; fresh gate and
branch review pending. No further source changes permitted during gate.
Hosted four-platform CI is NOT RUN on this unpublished branch.

## Branch review correction handoff

Candidate 105b34c received NEEDS REVISION in branch-r1.md: BR1, Medium,
positive scrollback witness admitted an omitted-setter counterexample.
Production setter was correct. Revise W2 to compare small and sufficiently
large limits after enough lines to cross historical page granularity.
Revised bundle 541ad4c1dc87dc5adf98f45ed7d00a99d536ac6b0bae3cc1143983e941e29cb2
approved in `.ayder/reviews/pyghostty-0.1.3-plan-r3.md`. Preserve original candidate and gate reports;
new candidate needs corrected before/after preservation, fresh full gate and
branch-r2 review. Source behavior and spec/operator rulings unchanged.

Plan-r3 handoff closed; approved digest revalidated. Corrected W2 ran against
baseline source/real 0.1.1 (2 passed) and candidate/locked 0.1.3 (2 passed).
Raw outputs: br1-preservation-before-corrected.txt and
br1-preservation-after-corrected.txt. BR1 changes proof only; next commit is
the new candidate, followed by fresh full gate and branch-r2 re-review.

## Version 0.0.5 amendment handoff

The migration candidate 7d725d6 received APPROVED branch-r2 and fresh PR review;
BR1 is closed. PR #4 was published after operator confirmation. Its hosted
four-platform CI passed; scheduled execution is skipped on PR events, while
the same-SHA local upgrade probe passed 11 ABI tests. Historical handoffs above
retain their original state and are superseded by this entry.

Operator now requests that the 0.0.5 package bump go into PR #4. This expands
the earlier implementation scope that held the package at 0.0.4. Treat the
metadata amendment as T1; native behavior and its approved T2 contracts stay
unchanged. Authority: .ayder/plans/version-0.0.5/note.md and linked checks.
Execution snapshot digest: cefef9d37296c7ad64f0c2a01cd0a37bf43bd412cd593b9ae6f02ea356b5a08a.
Baseline: 7d725d6423eb47213d0291ffe424ec5136e89e10; verified to match PR #4.
Coordinator changes only project/lock versions to 0.0.5, records this handoff,
then commits a new candidate for fresh independent full gate and branch review.
Evidence root: .ayder/evidence/version-0.0.5/. Publication authorization persists
for updating PR #4; no merge, tag or release is authorized.

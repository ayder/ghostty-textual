Reviewed: `.ayder/plans/pyghostty-0.1.3/plan.md` and linked witnesses, revision 3; baseline `45c2aa1fdf410b970e83a5ef60cb3af4cd556322`; current historical candidate `105b34c85afc61c036a17a78b8dce5203f7c07e4`
Verdict: APPROVED
Bundle SHA256: 541ad4c1dc87dc5adf98f45ed7d00a99d536ac6b0bae3cc1143983e941e29cb2

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

Supersedes: plan review r2 for execution of the revised W2 proof. Previous reviews remain historical; unchanged contracts and witnesses retain their prior approval.

Checked:

- Read branch review r1 and its BR1 counterexample, `br1-resolution.md`, revised K1 witness, plan, unchanged K2/K3 witnesses, prior plan review and the proposed test diff. The spec/product decision and production implementation are unchanged. W2 retains dimensions, disabled history and reset assertions while replacing the nondiscriminating upper bound with a real small-versus-large retention comparison after enough input to cross page boundaries.
- Ran `check-plan.py .ayder/plans/pyghostty-0.1.3/plan.md --table`; exit 0 and digest above. All AC/decision coverage remains present. Coverage is copied from the checker-generated table.
- Independently used isolated native handles, created/freed through each real version's CFFI API, to verify revised setup feasibility. With 17x4 terminals and `b"line\r\n" * 10000`, actual 0.1.1 retained 4673 for requested20 and 9997 for requested10000000. Actual 0.1.3 retained 2011 for line-limit20 and 4673 for line-limit10000000; omitting its setter also retained 4673. The revised strict comparison passes both characterized versions and fails when positive-limit configuration is omitted. It does not impose an invalid exact row bound or depend on matching retention across versions.
- Read the previous proof attempt's raw logs: small-versus5000 after5000 passed candidate but failed baseline because both baseline values retained4997. The resolution correctly marks that failed baseline attempt superseded; it is not represented as preservation proof. The new input/reference solves that observed page-floor problem without changing terminal behavior.
- Proposed test code implements the revised approved setup using two real Terminal instances and includes dimensions, zero-history and hard-reset checks. It creates/frees separate handles; test ownership remains serial. No subject, product or policy edits were made, and no shared test suite was run during this review. The only native executions were short independent handle probes.

Closed: BR1's plan/witness coverage correction is approved. Final branch closure still requires actual revised before/after preservation logs, corrected committed candidate, fresh full gate and branch re-review. The old candidate gate remains historical, and required hosted four-platform CI remains outstanding until authorized publication.

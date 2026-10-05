Reviewed: `.ayder/specs/pyghostty-0.1.3.md`, revision 1; baseline `45c2aa1fdf410b970e83a5ef60cb3af4cd556322`
Verdict: APPROVED

Findings: None.

Checked:
- Read AGENTS.md, `.ayder/policy.md`, the spec, ledger, and dev-contract-v2 spec/review/repository-setup references. The change meets the repository's T2 triggers; the required independent reviews, serial implementation, native coverage and four-platform gate are coherent. The explicit operator ruling permits continuing and authorizes the sole characterization change.
- Verified HEAD is the stated baseline; only the new policy/contract tree is untracked. No subject or product files were edited during review, and no shared test runs were launched.
- Opened the retained declaration diff, upstream pyproject and exploratory functional output. The bundled revision is `6467b1dab0be087fa8f0a7ccc7b3c5b88b9be7a5`. The exploratory result is 124 passed / 1 failed, with the existing 180-accent expectation the failure; it is correctly excluded from gate proof.
- Inspected the actual versioned declarations and headers in `/tmp/ghostty-api-investigation/`: construction accepts dimensions, scrollback uses a `size_t*` setter, colors use the generic render getter, and mode uses an initialized input/output configuration. The adapted exploratory sources use those call paths. Native functional output supports feasibility without proving the final implementation.
- Confirmed the header's clipboard contract requires a sized synchronous reply, returns void, denies absent replies, and permits `remember=false`. AC4 preserves existing opt-in/text/size/notification/error behavior while specifying the required new reply outcomes. Existing OSC 52 notification tests alone do not establish replies; the policy appropriately requires native integration plus reply/error branch coverage in the plan.
- Inspected `page.zig`'s `grapheme_max_len=64`, suffix storage truncation and append guard, plus its cap tests. The base is stored separately. AC5's 1 + 64*4 = 257-byte witness genuinely crosses the existing 256-byte initial buffer, while the excess-input expectation captures the authorized cap. Concrete codepoint selection and executable witness belong to the plan.
- Read the full CI workflow and current constructor, clipboard callback and grapheme regression test. AC1–AC6 describe observable outcomes, preserve lazy loading and public signatures, require the exact dependency and clean-wheel behavior, keep scheduled upgrade/`--no-sync`, and distinguish local validation from all four native platforms. The policy uses the workflow as the runnable command owner and preserves its Intel timing exception.
- Release publication is excluded; a later release needs a separate version bump and matching tag after CI. This avoids changing the release contract or representing exploratory evidence as a release candidate.

Remaining proof: The plan must provide AC witnesses and red/green/preservation evidence; the final candidate still requires current native validation, four-platform CI evidence and independent gate/branch reviews. These are later-phase obligations, not missing spec decisions.

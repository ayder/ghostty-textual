# Package version 0.0.5

**Tier:** T1 — bounded package metadata change; native implementation and characterized behavior are unchanged.
**Intent:** Add the operator-requested 0.0.5 package bump to PR #4.
**Premise:** Current PR head 7d725d6423eb47213d0291ffe424ec5136e89e10 identifies ghostty-textual as 0.0.4; GitHub has already published v0.0.4. The release workflow requires tag, package and archive versions to agree.
**Baseline:** 7d725d6423eb47213d0291ffe424ec5136e89e10.
**Evidence:** ../../evidence/version-0.0.5/.

AC1: Project and lock metadata identify ghostty-textual as 0.0.5 while pyghostty remains exactly 0.1.3.
AC2: Wheel and sdist metadata/filenames and the installed package __version__ identify 0.0.5; the full existing CI gate is preserved.
AC3: PR #4 includes the bump and its current validation status; no tag or release is created.

### Task K1: Update package version and PR handoff

**Depends:** none
**Create/modify:** pyproject.toml; uv.lock; .ayder/ledger.md; this note and linked checks; existing PR #4 title/body as needed.
**Contract:** Consume the reviewed pyghostty 0.1.3 integration; produce ghostty-textual 0.0.5 distribution metadata. Runtime __version__ already reads installed distribution metadata.
**Change:** Update only the project version and corresponding local project lock entry. Record the operator's expanded version-bump scope without rewriting historical migration contracts. Reuse existing tests and gate; verify release archive metadata and installed version directly. No additional source tests are needed for this reversible metadata edit. AC1–AC3.
**Witnesses:** [K1 named checks](tests/K1.md)
**Review:** Consistent metadata, unchanged dependencies/source/CI, correct archive and installed versions, accurate updated PR status.

## Delivery

Freeze a committed candidate, use a fresh independent full local gate, and obtain independent branch review of the amendment. Then push to PR #4 under the operator's existing publication authorization and check hosted CI. T1 note review is not routed by repository policy; record its execution snapshot digest before editing package metadata.

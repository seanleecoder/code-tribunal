# Platform differences

| Concern | GitLab | GitHub |
|---|---|---|
| Installation | Protected direct include or hardened child pipeline | Checked-in Actions workflow |
| Trusted workflow boundary | Protected template project/ref and variable boundary | Base-branch workflow selected for `pull_request`; never `pull_request_target` |
| Review target | Merge request | Pull request |
| Inline posting | Discussions/DiffNotes | Pull-request review comments |
| Summary and state | MR notes; state author must match token bot | PR issue comments; state author must match configured bot login |
| Commands | Reply in finding discussion; Developer/30+ | Reply to root inline comment; user-repository `OWNER`, or Write/Maintain/Admin verified with a fine-grained token (effectively required for organization repositories) |
| Thread resolution | GitLab discussion API | GraphQL; optional fine-grained resolve token |
| Merge enforcement | **Pipelines must succeed** | Gate job configured as required check |
| Fork behavior | Protected variables withheld; deployment topology determines whether trusted jobs run | External forks skipped by the canonical workflow |
| Concurrency | Post serialized with an MR-scoped resource group | Workflow concurrency groups by PR; in-progress runs are not cancelled |
| Diff collection | Paginated MR diff API; exact-path raw recovery for collapsed entries, with incomplete fallbacks rejected | Immutable base/head comparison raw diff; HTTP 406/too-large rejected |
| Artifact retention | 7 days for prepare/review/critique; 30 days for consensus/post/gate | Repository/organization Actions default |

Both platforms use the same configuration, reviewer adapters, artifact schemas,
consensus policy, posting reconciliation, and gate evaluator. Platform-specific
credentials are never passed into reviewer subprocess environments.

GitLab's deprecated `/changes?access_raw_diffs=true` endpoint is a conditional
compatibility fallback only; the paginated `/diffs` endpoint remains primary.
Fallback data is accepted only when the response explicitly reports
`overflow=false`, every affected old/new path has one exact match, and the
replacement has no `collapsed` or `too_large` flag. Prepare re-fetches the MR
diff version afterward and rejects any base, start, or head revision change.

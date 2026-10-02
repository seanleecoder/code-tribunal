# Evidence record: GitLab / older-format note refresh / 2026-10-02

Status: passed

Release-runtime-source: c525ccff5e90beab8f498410dd4e3fe36ce10031
Release-base-digest: sha256:6f2715a62bbdfe19a228848644ddef2fedcef17c4e769ac9b3c3775a16ecfe47
Release-reviewer-digest: sha256:1ee0b432fcedfc0e8bba11269f8cb2f410cfbe912ece808b54949dcff38cf8aa

> Sanitized operator record. No credentials, raw model bodies, or job traces are
> included.

## Identity and preconditions

- Consumer: public `seanleecoder/code-tribunal-demo`, project `84667714`.
- Change: [MR !11](https://gitlab.com/seanleecoder/code-tribunal-demo/-/merge_requests/11),
  preserved branch `evidence/chain-b-88bc941`, reopened for this check and closed afterwards.
- Subject: bot root note [`3601861614`](https://gitlab.com/seanleecoder/code-tribunal-demo/-/merge_requests/11#note_3601861614),
  created `2026-07-25T20:47:29.348Z`, previously updated `2026-07-25T21:06:54.381Z`.
- Finding identity: `4f51a7af75ec457a69f687be2e15363c9c59b047290f8efdcda8784aa2fa9ff9`.
- Demo head: `dc5902d2161ccfc801a26e9fa6fcd3221fb9223b`; temporary trusted template commit: `9b62c4ce9483b70ab17be2962111b06173b5854e`.
- Parent pipeline: [`2906159298`](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/2906159298).
- Child pipeline: [`2906159343`](https://gitlab.com/seanleecoder/code-tribunal-demo/-/pipelines/2906159343).
- Base image: `ghcr.io/seanleecoder/code-tribunal/ai-review-base:2.0-c525ccff5e90beab8f498410dd4e3fe36ce10031@sha256:6f2715a62bbdfe19a228848644ddef2fedcef17c4e769ac9b3c3775a16ecfe47`.
- Reviewer image: `ghcr.io/seanleecoder/code-tribunal/ai-review-reviewer:2.0-c525ccff5e90beab8f498410dd4e3fe36ce10031@sha256:1ee0b432fcedfc0e8bba11269f8cb2f410cfbe912ece808b54949dcff38cf8aa`.

The original protected fixture branch and existing discussion were reused. Only
its CI includes were temporarily updated to the current hardened topology and
the candidate template commit. The fixture and anchor stayed unchanged. The
temporary template selected the original three reviewer identities (Claude,
Codex, OpenCode), explicitly allowed local mocks, and disabled require-real flags.
The temporary project scenario was `blocking_alt`, matching the original body.
No provider calls were made.

## Actual result

The child pipeline and `post` both succeeded. The post result reported
`updated_discussions: 1`, `created_discussions: 0`, `resolved_discussions: 0`,
`skipped_unchanged: 0`, and an empty warnings list. Its only publication action
updated the existing root note in discussion `903db1c64759208baeaa1776a1d8114392dbb8f4`.

The same note now has `updated_at: 2026-10-02T09:44:40.015Z`. Its original creation
time, author ID `40508593`, and full finding identity are unchanged.
The old `Consensus:` footer is replaced by `Support:`; the hidden `ai-review:v1`
identity remains recoverable without a legacy footer parser.

## Cleanup and audit

- MR !11 is closed again. Its original CI file was restored, the temporary
  scenario variable was deleted, and the temporary template branch was deleted.
  The preserved fixture branch and refreshed discussion remain available.
- `make demo-preflight` reported both demos ready after cleanup.
- Inspected the post artifact and REST note metadata before and after.
  `scripts/scan_evidence_leaks.py` found no hits in the downloaded post JSON.
  Raw traces and reviewer artifacts were not included in this scan; an
  exact-secret-value audit was not performed.
- The full candidate canary separately verified this same image pair's source
  labels and provenance ([record](record-candidate-canary.md)).

## Verdict

Scoped pass. The frozen candidate refreshed a GitLab note authored by a released
1.0.0 image to the current `render-body.v4` presentation in place, with one update,
no newly created discussion, and preserved identity and authorship. Long-body
truncation and other historical body variants were not exercised.

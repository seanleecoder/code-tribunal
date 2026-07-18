# Release process

1. Land behavior, schema, migration, and documentation changes on one reviewed
   source commit.
2. Run `make quality` and required hostile/local regression suites.
3. Build base and reviewer images from that exact source commit.
4. Record image digests and attestations; verify anonymous digest pulls.
5. Run the GitHub and GitLab current-image evidence matrix.
6. Update canonical templates so source SHA and both digests move together.
7. Re-run the supply-chain and documentation pin checks.
8. Update the changelog, version/release record, and evidence index.
9. Tag the exact reviewed source and publish release notes.

Do not describe 1.0 as stable until the required live evidence is complete.
Never rebuild a release tag from a different source commit; publish a new patch
release instead.

# Temporary runner-profile inputs

These unmodified Apache-2.0 inputs come from Moby profiles commit
`f862455628f78f84c937e967e4a03902fb1dee11`:

- [`seccomp/default.json`](https://github.com/moby/profiles/blob/f862455628f78f84c937e967e4a03902fb1dee11/seccomp/default.json)
- [`apparmor/template.go`](https://github.com/moby/profiles/blob/f862455628f78f84c937e967e4a03902fb1dee11/apparmor/template.go)

Copyright The Moby Authors; the Apache-2.0 license text is the repository
[`LICENSE`](../../LICENSE). `codex_runner_profiles.py` checks their exact SHA-256
values, renders a named profile and records the explicit experimental changes.
The generated candidate is modified; these inputs are not.

COMPAT-012 covers these temporary inputs. They do not ship in reviewer images,
define a production policy, or replace the trusted registry. Delete them with
the investigation tooling before shipping an accepted launch arrangement.

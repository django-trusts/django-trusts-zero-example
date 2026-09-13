# Where Trusts fits in this example

This note is the issue #16 record of what Trusts does on its own and what
the example still has to do. It is not a claim that the declarative
authorization thesis is complete.

Example behavior changes are recorded in [migrates.md](../migrates.md).
django-trusts-zero owns the concrete Trust/Content models, stored
grants, and `TrustModelBackend`. Schema-neutral django-trusts is the
library Zero depends on. This example pins the #131 E-methods pair: Zero
Z-methods merge `2e3cccedb92b4cf85e9d6a3cd2d51821aad1d716` with Core
C-methods merge `f5211c11047eb6810680f5d1b13bf34b2c376635`. It does not
register a Project permission condition.

Inspected for this revision:

| Tree | Commit | What it is |
| --- | --- | --- |
| `django-trusts-example` default `master` | pre-#5 | Demo against Trusts post-#19; group attach implied access. |
| Historical `DJANGO-TRUSTS-8-Edit-Perm-Pages` / PR #1 | `54e83b76fee2e6e950cec94366adec038ebc1260` | Incomplete Project / collaborator UI on Django 1.8 / Python 2. |
| Example `dev` baseline (pre-#14) | `1e12335821d698b7cd4fcc822addde7c054f7dea` | Alice/Bob demo on Zero PR #20 / core PR #121. |
| Example `dev` #142 E-convert (#15) | `0cb7ea23708610e467915abd4c2f768b5aece839` | Builders + pair Core `710b3ea` / Zero `bceb024`. |
| `django-trusts-zero` Z1 (merged #31) | prior E1 pin | Historical Z1 donation API. |
| `django-trusts` C1 (merged #158) | prior E1 pin | Historical public AnyPath API. |
| `django-trusts-zero` Z-methods (merged #33) | `2e3cccedb92b4cf85e9d6a3cd2d51821aad1d716` | Configured-backend donation; `Trust:own` stays a builder. |
| `django-trusts` C-methods (merged #172) | `f5211c11047eb6810680f5d1b13bf34b2c376635` | `backend.register_relationship` / `backend.add_named_filter`. |

The historical branch is the useful ancestor for *domain shape* (a `Project`
`Content` subclass, settlor trusts, collaborators, groups). Zero ships
`ContentQuerySet.permitted` and `Trust.objects.filter_by_user_content_perm`.
Trustee and TrustGroup writes are explicit ORM rows (`Content.grant` /
`Trust.grant_group_permission` are gone). Group-derived access requires
the TrustGroup local/global intersection.

## Trusts fits naturally

- `Project(Content)` plus `trusts.zero.backends.TrustModelBackend` so `user.has_perm('projects.read_project', project)` is object-level.
- Creating a dedicated `Trust` per project (settlor = creator) and writing `TrustUserPermission` rows for the owner.
- Grant / revoke trustees as insert / delete of `TrustUserPermission`.
- Organization membership as Django `Group` **associated** with a trust
  (`TrustGroup`), with the `editor` role as the **global ceiling** and
  per-project `TrustGroup.permissions` as the local subset. Role-derived
  capabilities are ceiling only; they are not a local assignment.
- Public read as `public-readers` associated with that project's trust
  **and** a local `read_project` grant. Same tables `has_perm` reads.
  Existing accounts are synced into the group at seed; create/edit forms
  and admin keep every user in that group (`post_save` + `m2m_changed` +
  `UserAdmin.save_related`). Association without the local grant grants
  nothing. `public-readers` is a system-maintained audience for every
  signed-in account.
- Team mutations via `trusts.zero.authorization` (`associate_group_with_trust`,
  `set_trust_group_permissions`, `disassociate_group_from_trust`). Writes
  outside the ceiling raise `AuthorizationDenied` and do not mutate.
- View guards via `trusts.decorators.permission_required` and `K()`.
- Cross-organization isolation: Dave's notes are on Dave's trust; Alice's
  grants do not leak.
- Same team, different projects: `acme-staff` has `change` on Acme Playbook
  and `read` only on Acme Handbook.

## Application code that remains

- **List filter.** `projects.query.readable_projects` wraps
  `Project.objects.permitted` (core SQL: trustee **or** TrustGroup
  local/global intersection). Pagination wraps that QuerySet. Inactive and
  anonymous principals are empty, matching `User.has_perm`.
- **Grant / revoke / visibility / team helpers.** Thin writes to Zero
  tables. Visibility creates `TrustGroup` + `TrustGroupPermission` for
  local public read.
- **Create flow.** Allocate a unique slug, then create trust + project +
  owner grants in one transaction. Trusts does not auto-grant the settlor.
- **Public-readers enrollment.** Application signals, admin `save_related`,
  and seed sync write the group membership Trusts already evaluates. Not a
  per-request predicate. See [migrates.md](../migrates.md).
- **UI and seed.** Forms, templates, `seed_demo`, demo passwords. Project
  settings distinguish association, local rights, and the global ceiling.

## Model limitations (not papered over)

- `Trust.trust` and `Trust.settlor` are readonly after create. A project
  cannot be moved to another organization trust. Visibility change attaches
  or detaches a group on the *existing* trust (and enables or drops local
  read).
- Grants are **trust-scoped**. Two projects on one trust share trustees and
  TrustGroup local rights. This demo gives most projects their own trust so
  visibility and collaborator lists stay per-object. The Acme Handbook
  shares the Acme trust with any future Acme content — that is the intended
  organization pattern and also the limitation. **Acme Appendix** is seeded on
  that same Trust so the UI can show Trust-scoped local rights. Acme Playbook
  has its own Trust so the same team can have different local rights.
- Django `User.has_perm` short-circuits for `is_superuser`. Superusers are
  not a Trusts proof. Seed users are ordinary users. The list helper does
  **not** special-case superusers; a superuser may see a narrower list than
  `has_perm` would allow. That mismatch is documented, not treated as
  Trusts validation.
- `:own` on `Trust` is a **registration-time builder** donated by Zero
  during `ZeroConfig.ready()`:

  ```python
  backend.add_named_filter(
      Trust, "own", predicate=lambda u, p, o: u == o.settlor,
  )
  ```

  Core invokes the callable once with symbolic refs (zero SQL on the
  first-party donation path), stores only normalized IR, and never runs
  it during `has_perm` or `.permitted()`. Object checks and
  `Trust.objects.permitted("change:own", user)` share that IR. This
  example does not register a Project condition and does not use `:own`
  for project list membership. Public / private is a group grant, not a
  condition code. Application modules do not import `condition_refs`,
  `Expr`, or other Core condition-node constructors.
  `TRUSTS_ALLOW_LEGACY_PERMISSION_CALLBACKS` is unset; if it were
  `True` it would be `trusts.E007` and would not restore runtime
  callbacks. `manage.py check` must stay clean of `trusts.E001` /
  `trusts.E007`.

Windows ACL work stays on django-trusts#17 and is out of this issue.
Parent Trust inheritance and explicit deny stay out of scope. Core
builder registration is available; this demo does not register a
Project condition.

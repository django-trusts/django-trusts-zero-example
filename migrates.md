# Migration record (django-trusts-example)

This record covers **example application** API and method behavior changes
while modernizing the demo for django-trusts `1.0.0.dev0`
(`5bd2a585d3806571f95bebf89c2852cac3649d25`).

**The django-trusts library API and method signatures are unchanged.** There
is no companion core PR and no update to django-trusts `migrates.md`.
Authorization decisions still go through `TrustModelBackend` / Trusts tables.

## No change to these Trusts call sites

- `User.has_perm` / `User.has_perms` via `trusts.backends.TrustModelBackend`
- `trusts.decorators.permission_required`, `P`, `K`
- `Trust.objects.get_or_create_settlor_default`, `get_root`, `filter_by_content`,
  `filter_by_user_perm`
- `Content` subclassing, `Meta.roles`, `create_trust_root`,
  `update_roles_permissions`
- `TrustUserPermission` and `Trust.groups` as the grant store

## Changes (example only)

### 1. `readable_projects` / `editable_projects` deny inactive users

| | |
| --- | --- |
| Previous | Helpers skipped anonymous users only. An inactive user with trustee or group rows still appeared in the list QuerySet while `User.has_perm` returned `False`. |
| New | `projects.query.projects_with_perm` returns `Project.objects.none()` when the principal is missing, anonymous, unauthenticated, or `is_active` is false. |
| Replacement | Same function names. Callers do not change. |
| Affected | `project_list` (`can_edit_ids` and the page QuerySet). Tests that compare list membership to `has_perm`. |
| Authorization | List and direct-object deny now agree for inactive users. Superer short-circuit on `has_perm` is unchanged Django behavior and is not treated as Trusts validation. |

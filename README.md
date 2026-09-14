# django-trusts-example

A runnable Django 6.1 reference application for **declarative, relational
object permissions** with
[django-trusts-zero](https://github.com/django-trusts/django-trusts-zero).

The Alice/Bob/Carol/Dave project workspace makes the permission model visible:

- owners and directly named trustees receive per-object rights;
- teams have a global ceiling plus a per-Trust selection;
- one Trust can protect several objects, while another gives the same team
  different rights;
- public access is an ordinary group relationship, not application magic;
- list authorization happens in the database before pagination, and direct
  URLs fail closed through the same policy.

This is the implementation repository for
[django-trusts #16](https://github.com/django-trusts/django-trusts/issues/16).
It demonstrates the historical Trust/Content schema supplied by Zero on top
of schema-neutral
[django-trusts](https://github.com/django-trusts/django-trusts).

Requires **Python ≥ 3.12**.

## Run the demo

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Open http://127.0.0.1:8000/ and log in. Every seeded password is `demo`.

| User | What to try |
| --- | --- |
| `alice` | Owns private notes, a shared roadmap, a public changelog, the Acme handbook and appendix on one Trust, and the Acme playbook on another |
| `bob` | Has direct **read** access to Shared Roadmap |
| `carol` | Belongs to `acme-staff`; can **read** the shared-Trust handbook and appendix, and **change** the separately protected playbook |
| `dave` | Is an outsider with his own notes; reads Alice's work only when it is public |

`acme-staff` has the **editor** role as its global ceiling (read + change).
Each Trust selects a local subset of that ceiling. Associating the team
without local rights grants nothing. Handbook and Appendix share
`org:acme`, so one local change applies to both; Playbook uses another
Trust, allowing different rights for the same team.

## Demo workflows

1. **Create an object** — as Alice or Bob, choose *New project*. The creator
   receives read and change trustee rows on a new Trust.
2. **Change visibility** — on a project you can change, toggle public.
   Public visibility associates `public-readers` and grants local read on
   that Trust. Association alone grants nothing. Log in as Dave, or create
   another account, to see the list change.
3. **Grant or revoke a trustee** — give Bob or Dave read, or read + change,
   and then revoke it.
4. **Configure a team** — associate `acme-staff` without granting access,
   then enable local **Read** and/or **Change** within its global ceiling.
   Rights apply to every project protected by that Trust. An
   outside-the-ceiling write is rejected without mutation.
5. **Compare users** — the home page is already filtered for Bob, Carol, or
   Dave.
6. **Try a denied path** — as Bob, open Alice Private Notes (403) or edit
   Shared Roadmap (403). As Carol, editing the read-only handbook or appendix
   is denied, while editing the playbook succeeds.

List pages paginate *after* the authorization filter (`PROJECT_PAGE_SIZE`,
default 3) from `Project.objects.permitted`. Direct URLs use the same
`has_perm` decision as list membership.

## Verify it

```bash
python manage.py check
python manage.py test projects
```

`manage.py check` must remain clean of `trusts.E001` (invalid stored
condition IR) and `trusts.E007` (leftover
`TRUSTS_ALLOW_LEGACY_PERMISSION_CALLBACKS`). Named conditions are
registration-time builders. The example does not set that leftover flag
and does not register a Project condition.

CI tests Python 3.12–3.14 with Django 6.1 and SQLite, including a fresh
migration and seed plus collected static files. A separate MySQL 8 job runs
the same system check, migration, seed, and authorized-list smoke path.

## Configuration

The application retargets Core to the reviewed C2 standalone-OrderedFold
extraction and keeps Zero on the merged #191 decorator-family revision:
[django-trusts-zero at `517307170f954f187da78c56e236ec1779c46e29`](https://github.com/django-trusts/django-trusts-zero/commit/517307170f954f187da78c56e236ec1779c46e29)
(legacy request family, merged #36; includes Z-methods #33) with
[schema-neutral django-trusts at `9875c02571b9b978c27b26ce48d0c62154547a86`](https://github.com/django-trusts/django-trusts/commit/9875c02571b9b978c27b26ce48d0c62154547a86)
(C2, merged #197). The exact compatible revisions are pinned in
`requirements.txt` and `pyproject.toml`.

The relevant Django settings are:

```python
INSTALLED_APPS = [
    # Django applications...
    "trusts.zero.apps.ZeroConfig",
    "projects.apps.ProjectsConfig",
]

AUTHENTICATION_BACKENDS = [
    "trusts.zero.backends.TrustModelBackend",
]
```

`ProjectsConfig.ready()` donates `Project` with
`register_zero_content(backend, Project)`. ZeroConfig owns Trust TUP/TGP
through `backend.register_relationship`. The helper is required because
Zero does not auto-discover host Content terminals.
Zero donates `Trust:own` during startup as a registration-time builder:

```python
backend.add_named_filter(
    Trust, "own", predicate=lambda u, p, o: u == o.settlor,
)
```

Core invokes that callable once with symbolic refs, stores only the
normalized predicate, and never runs it during `has_perm` or
`.permitted()`. This example does not register a Project condition and
does not import `condition_refs`, `Expr`, or other Core condition-node
constructors. The concrete Trust, Content, stored-grant,
authorization-helper, and legacy request-decorator APIs live under
`trusts.zero.*`. View guards import `permission_required` and `K` from
`trusts.zero.decorators`.

## Deploy on Dokku

The repository includes deployment glue for Dokku with linked MySQL 8
(`DATABASE_URL` from dokku-mysql). Django 6.1 requires **MySQL 8.4+**.
Local `runserver` uses SQLite when `DATABASE_URL` is unset.

Current development is preserved on the `dev` branch while `master`
retains the pre-Zero baseline. That shared baseline is tagged
[`dev_split_core_attempt_1`](https://github.com/django-trusts/django-trusts-example/releases/tag/dev_split_core_attempt_1)
at `2ee36f93`. Deploy `dev` as Dokku's application branch:

```bash
git remote add dokku dokku@your-host:your-app
git push dokku dev:master
```

The Procfile release phase runs `migrate --noinput`. Zero retains the
historical migration loader keys `trusts.0001_initial` and
`trusts.0002_trustgroup`, so this change adds no new Trusts schema.

Static files are collected in steps whose output becomes part of the web
image:

- The Herokuish Python buildpack runs `collectstatic --noinput` at compile
  time. Leave `DISABLE_COLLECTSTATIC` unset.
- `app.json` runs `collectstatic --noinput --skip-checks` during Dokku
  predeploy, which commits those changes into the image without requiring
  MySQL during that step.

Seed a new database once after the first successful release:

```bash
dokku run your-app python manage.py seed_demo
```

`seed_demo` is idempotent, but it should not run on every release. On an
existing deployment, run it only if the TrustGroup rows are missing.

For HTTPS login POSTs, configure the public origin:

```bash
dokku config:set your-app CSRF_TRUSTED_ORIGINS=https://your-app.example.com
```

Comma-separated extra origins are supported.

| Variable | Default |
| --- | --- |
| `SECRET_KEY` | Hard-coded demo key |
| `CSRF_TRUSTED_ORIGINS` | Empty; set it for HTTPS |
| `ALLOWED_HOSTS` | `*` for this demo |

WhiteNoise serves collected static files, Gunicorn runs `example.wsgi` on
`$PORT`, and PyMySQL plus `cryptography` supports MySQL 8 without native
client headers.

## Design notes and migration history

The historical `DJANGO-TRUSTS-8-Edit-Perm-Pages` branch (PR #1) supplied
the original Project/collaborator domain idea. This version runs that idea
on the maintained Zero/Core split.

See [docs/TRUSTS_FIT.md](docs/TRUSTS_FIT.md) for the model fit and
[migrates.md](migrates.md) for the exact old/new API, behavior, and
migration-bot checklist.

"""Application helpers around Zero tables.

Trustee writes are explicit TrustUserPermission rows (Content.grant /
revoke are gone). Group-derived access uses the TrustGroup local/global
intersection: association alone grants nothing; local TrustGroup
permissions must also sit inside Group.permissions (the global ceiling).

public-readers is a system-maintained audience: every User row is kept in
that group so Zero can grant public read through ordinary group membership.
Forms and admin must not be able to drop it; see signals and UserAdmin.
Public visibility still associates that group, then enables local read.
"""

import threading

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.db.utils import OperationalError, ProgrammingError

from trusts.zero.models import (
    TrustGroup,
    TrustGroupPermission,
    TrustUserPermission,
)
from trusts.zero.policy import (
    get_group_global_ceiling,
    permission_in_global_ceiling,
)

from .models import Project

_enrolling = threading.local()

PUBLIC_GROUP_NAME = "public-readers"
READ = "read_project"
CHANGE = "change_project"

LOCAL_PERM_LABELS = {
    READ: "Read",
    CHANGE: "Change",
}
LOCAL_GRANTABLE = (READ, CHANGE)


def project_permission(codename):
    return Permission.objects.get(
        content_type=ContentType.objects.get_for_model(Project),
        codename=codename,
    )


def project_content_type():
    return ContentType.objects.get_for_model(Project)


def grant_user(project, user, *codenames):
    """Grant trustee rows on the project's trust (explicit TUP writes)."""
    for codename in codenames:
        TrustUserPermission.objects.get_or_create(
            trust=project.trust,
            entity=user,
            permission=project_permission(codename),
        )


def revoke_user(project, user, *codenames):
    """Remove trustee rows. Empty codenames removes every grant for the user."""
    qs = TrustUserPermission.objects.filter(trust=project.trust, entity=user)
    if not codenames:
        qs.delete()
        return
    for codename in codenames:
        qs.filter(permission=project_permission(codename)).delete()


def public_readers_group():
    group, _ = Group.objects.get_or_create(name=PUBLIC_GROUP_NAME)
    group.permissions.add(project_permission(READ))
    return group


def enroll_in_public_readers(user):
    """Attach one account to public-readers so Trusts group grants apply."""
    if user is None or not getattr(user, "pk", None):
        return
    if getattr(_enrolling, "busy", False):
        return
    _enrolling.busy = True
    try:
        public_readers_group().user_set.add(user)
    except (ProgrammingError, OperationalError, Permission.DoesNotExist, ContentType.DoesNotExist):
        # Migrations / early User inserts before Project permissions exist.
        return
    finally:
        _enrolling.busy = False


def sync_public_readers():
    """Enroll every existing user. Complements the post_save signal for new ones."""
    try:
        group = public_readers_group()
    except (ProgrammingError, OperationalError, Permission.DoesNotExist, ContentType.DoesNotExist):
        return None
    User = get_user_model()
    group.user_set.add(*User.objects.all())
    return group


def is_public(project):
    """True when public-readers has *effective* read on this project's Trust.

    Association alone is not public. The same intersection ``has_perm`` uses
    must hold: TrustGroup row, local ``read_project``, and that permission
    inside the group's global ceiling.
    """
    read = project_permission(READ)
    tg = (
        TrustGroup.objects.filter(
            trust=project.trust,
            group__name=PUBLIC_GROUP_NAME,
            permissions=read,
        )
        .select_related("group")
        .first()
    )
    if tg is None:
        return False
    return permission_in_global_ceiling(tg.group, read)


def set_public(project, make_public):
    """Visibility is trust-scoped: attach or detach the public-readers group.

    Trust.trust / Trust.settlor are readonly after create, so a project cannot
    be moved to another trust. Public read associates public-readers and
    enables local read_project. Association without that local grant grants
    nothing (fail closed).
    """
    group = public_readers_group()
    if make_public:
        tg, _ = TrustGroup.objects.get_or_create(trust=project.trust, group=group)
        TrustGroupPermission.objects.get_or_create(
            trustgroup=tg,
            permission=project_permission(READ),
        )
    else:
        project.trust.groups.remove(group)


def grant_local_group_permission(project, group, *codenames):
    """Enable local TrustGroup grants. Permission instances must be in the ceiling."""
    tg, _ = TrustGroup.objects.get_or_create(trust=project.trust, group=group)
    for codename in codenames:
        TrustGroupPermission.objects.get_or_create(
            trustgroup=tg,
            permission=project_permission(codename),
        )


def trustee_rows(project):
    return (
        TrustUserPermission.objects.filter(trust=project.trust)
        .select_related("entity", "permission")
        .order_by("entity__username", "permission__codename")
    )


def ceiling_project_codenames(group):
    """Project read/change permissions inside the group's global ceiling."""
    return set(
        get_group_global_ceiling(group)
        .filter(
            content_type=project_content_type(),
            codename__in=LOCAL_GRANTABLE,
        )
        .values_list("codename", flat=True)
    )


def unassociated_groups(project):
    """Groups that can still be associated (public-readers is visibility-only)."""
    associated_ids = project.trust.groups.values_list("pk", flat=True)
    return Group.objects.exclude(pk__in=associated_ids).exclude(name=PUBLIC_GROUP_NAME).order_by("name")


def team_setting_rows(project):
    """Associated teams with ceiling, local selection, and effective intersection."""
    rows = []
    trustgroups = (
        TrustGroup.objects.filter(trust=project.trust)
        .select_related("group")
        .prefetch_related("permissions", "group__roles")
        .order_by("group__name")
    )
    for tg in trustgroups:
        ceiling_codes = ceiling_project_codenames(tg.group)
        local_codes = set(tg.permissions.values_list("codename", flat=True))
        effective_codes = [
            code for code in LOCAL_GRANTABLE if code in local_codes and code in ceiling_codes
        ]
        stale = [code for code in LOCAL_GRANTABLE if code in local_codes and code not in ceiling_codes]
        grantable = [(code, LOCAL_PERM_LABELS[code]) for code in LOCAL_GRANTABLE if code in ceiling_codes]
        role_names = list(tg.group.roles.values_list("name", flat=True))
        rows.append(
            {
                "group": tg.group,
                "is_public_readers": tg.group.name == PUBLIC_GROUP_NAME,
                "ceiling_codenames": [code for code in LOCAL_GRANTABLE if code in ceiling_codes],
                "ceiling_labels": [
                    LOCAL_PERM_LABELS[code] for code in LOCAL_GRANTABLE if code in ceiling_codes
                ],
                "role_names": role_names,
                "local_codenames": local_codes,
                "effective_codenames": effective_codes,
                "effective_labels": [LOCAL_PERM_LABELS[code] for code in effective_codes],
                "stale_local_labels": [LOCAL_PERM_LABELS[code] for code in stale],
                "grantable": grantable,
                "grants_nothing": not effective_codes,
            }
        )
    return rows

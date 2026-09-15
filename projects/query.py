"""Declarative list filters against Zero relational tables.

``Project.objects.permitted`` is the Zero SQL filter: trustee grants or the
TrustGroup local/global intersection (same tables ``has_perm`` reads).
Pagination must wrap the returned QuerySet, not the unfiltered table.
"""

from django.db.models import QuerySet

from .models import Project


def projects_with_perm(user, codename) -> QuerySet[Project]:
    # permitted() already denies anonymous/inactive principals. Superuser
    # short-circuit on has_perm is a Django limitation (documented).
    return Project.objects.permitted(codename, user).order_by("title", "pk")


def readable_projects(user) -> QuerySet[Project]:
    return projects_with_perm(user, "read_project")


def editable_projects(user) -> QuerySet[Project]:
    return projects_with_perm(user, "change_project")

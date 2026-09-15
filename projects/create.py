"""Atomic project creation. Application code — not a Trusts API."""

from django.db import transaction
from django.utils.text import slugify

from trusts.zero.models import Trust

from .grants import CHANGE, READ, grant_user
from .models import Project

# Trust.title is max_length=40; prefix "project:" is 8 characters.
TRUST_TITLE_PREFIX = "project:"
SLUG_MAX_LENGTH = 40 - len(TRUST_TITLE_PREFIX)


def unique_project_slug(title):
    """Return a Project.slug that is free and fits the trust title column."""
    base = slugify(title)[:SLUG_MAX_LENGTH] or "project"
    slug = base
    n = 2
    while Project.objects.filter(slug=slug).exists():
        suffix = f"-{n}"
        slug = f"{base[: SLUG_MAX_LENGTH - len(suffix)]}{suffix}"
        n += 1
    return slug


@transaction.atomic
def create_owned_project(user, title, description=""):
    """Create trust, project, and owner grants in one transaction."""
    slug = unique_project_slug(title)
    trust = Trust(
        settlor=user,
        title=f"{TRUST_TITLE_PREFIX}{slug}",
        trust=Trust.objects.get_root(),
    )
    trust.save()
    project = Project(
        title=title,
        description=description,
        trust=trust,
        slug=slug,
    )
    project.save()
    grant_user(project, user, READ, CHANGE)
    return project

from django.db import models
from django.urls import reverse
from django.utils.text import slugify

from trusts.zero.models import Content


class Project(Content):
    """A Content subclass: each project is authorized through its Trust."""

    title = models.CharField(max_length=80)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        default_permissions = ("add", "read", "change", "delete")
        ordering = ("title",)
        # Roles are declarative permission bundles. update_roles_permissions
        # materializes them; they are not Python predicates.
        roles = (
            ("reader", ("read_project",)),
            ("editor", ("read_project", "change_project")),
        )

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("project-detail", kwargs={"pk": self.pk})

    def __str__(self):
        return self.title

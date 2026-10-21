"""Projects app config.

Each app owns its recorder. ``ready`` records Project, then
``TrustsConfigMixin.ready`` freezes that recorder. Zero's recorder
is not this app's. ``INSTALLED_APPS`` order does not matter.
"""

from trusts.apps import TrustsConfigMixin
from trusts.zero.registration import register_zero_content


class ProjectsConfig(TrustsConfigMixin):
    default_auto_field = "django.db.models.AutoField"
    name = "projects"
    verbose_name = "Projects"

    def ready(self):
        # Forced: projects.models imports trusts.zero.models.Content.
        # Content's manager module imports trusts.models, which imports
        # auth.Permission. projects.signals imports that chain and calls
        # get_user_model() at module level. Neither import is safe while
        # Django is still loading app configs.
        from .models import Project
        from . import signals  # noqa: F401 — enroll new users in public-readers

        register_zero_content(self.recorder, Project)
        super().ready()

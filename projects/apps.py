"""Projects app config.

Project is registered at the end of ``models.py`` through
``ProjectsBackend.get_registry()``. This config subclasses
``TrustsConfigMixin`` so that backend has a registration log.
``INSTALLED_APPS`` order does not matter. ``ready`` connects signals
and does not register.
"""

from trusts.apps import TrustsConfigMixin


class ProjectsConfig(TrustsConfigMixin):
    default_auto_field = "django.db.models.AutoField"
    name = "projects"
    verbose_name = "Projects"

    def ready(self):
        # Forced: projects.signals imports projects.grants, which imports
        # trusts.zero.models. That module's manager imports trusts.models,
        # which imports auth.Permission. projects.signals also calls
        # get_user_model() at module level. Neither import is safe while
        # Django is still loading app configs.
        from . import signals  # noqa: F401 — enroll new users in public-readers

        super().ready()

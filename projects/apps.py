from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    default_auto_field = "django.db.models.AutoField"
    name = "projects"
    verbose_name = "Projects"

    def ready(self):
        from . import signals  # noqa: F401 — enroll new users in public-readers
        self._register_project_with_zero()

    def _register_project_with_zero(self):
        """Donate Project as a Zero content terminal (TUP + both TGP plans).

        ZeroConfig registers Trust-as-content only. Host Content subclasses
        must call register_zero_content(handle, Model) or permitted()/has_perm
        stay empty. Z1 donation is idempotent; do not key off handle.registry.
        """
        from trusts.zero.apps import CANONICAL_BACKEND_PATH, zero_config
        from trusts.zero.registration import register_zero_content

        from .models import Project

        handle = zero_config().configured_backend(CANONICAL_BACKEND_PATH)
        register_zero_content(handle, Project)

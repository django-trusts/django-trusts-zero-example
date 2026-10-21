"""Projects authentication backend.

The catalog belongs to ``ProjectsConfig``. Do not borrow Zero's recorder.
"""

from django.contrib.auth.backends import ModelBackend

from trusts.backends import TrustModelBackendMixin


class ProjectsBackend(TrustModelBackendMixin, ModelBackend):
    """Listed backend for projects registrations."""

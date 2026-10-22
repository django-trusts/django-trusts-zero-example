"""Projects authentication backend.

The catalog belongs to ``ProjectsConfig``. Project is registered on
this backend's registry. Do not register it on Zero's backend.
"""

from django.contrib.auth.backends import ModelBackend

from trusts.backends import TrustModelBackendMixin


class ProjectsBackend(TrustModelBackendMixin, ModelBackend):
    """Listed backend for projects registrations."""

#!/usr/bin/env python
"""Fresh MySQL seed + Zero list-filter check (CI mysql-smoke job)."""

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "example.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.db import connection

from projects.query import readable_projects

if connection.vendor != "mysql":
    raise SystemExit(f"expected mysql vendor, got {connection.vendor}")

User = get_user_model()
alice = User.objects.get(username="alice")
bob = User.objects.get(username="bob")

alice_titles = list(readable_projects(alice).values_list("title", flat=True))
bob_titles = list(readable_projects(bob).values_list("title", flat=True))

expected_alice = [
    "Acme Appendix",
    "Acme Handbook",
    "Acme Playbook",
    "Alice Private Notes",
    "Public Changelog",
    "Shared Roadmap",
]
expected_bob = ["Public Changelog", "Shared Roadmap"]

if alice_titles != expected_alice:
    raise SystemExit(f"alice titles {alice_titles!r} != {expected_alice!r}")
if bob_titles != expected_bob:
    raise SystemExit(f"bob titles {bob_titles!r} != {expected_bob!r}")

print("mysql-smoke-ok")

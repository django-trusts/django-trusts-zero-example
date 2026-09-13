import ast
import json
import re
from importlib.metadata import distribution
from pathlib import Path
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.core import checks as django_checks
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import QuerySet
from django.forms import modelform_factory
from django.test import RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from trusts.core import TrustsRegistry, any_plan_records
from trusts.zero.apps import CANONICAL_BACKEND_PATH, zero_config
from trusts.zero.models import Role, Trust, TrustGroup, TrustGroupPermission, TrustUserPermission
from trusts.zero.registration import (
    donate_content_permission_conditions,
    donate_installed_permission_conditions,
)

from .demo import seed_demo
from .grants import (
    CHANGE,
    PUBLIC_GROUP_NAME,
    READ,
    grant_user,
    is_public,
    project_permission,
    public_readers_group,
    set_public,
)
from .models import Project
from .query import editable_projects, readable_projects

User = get_user_model()

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_PIN_SHA = "e9fd4cd4f77624f3d5351b505808c1d6fa8bcbc4"
ZERO_PIN_SHA = "fb32d70e82f6a63d03287eb959732db52bd266c8"
FORBIDDEN_CONDITION_NODES = frozenset(
    {
        "condition_refs",
        "principal_ref",
        "permission_ref",
        "object_ref",
        "Expr",
        "Const",
        "Eq",
        "Ne",
        "And",
        "Or",
    }
)


def _pin_sha_from_requirements(package):
    text = (REPO_ROOT / "requirements.txt").read_text()
    match = re.search(
        rf"{re.escape(package)} @ git\+https://github\.com/[^@\s]+@([0-9a-f]{{40}})",
        text,
    )
    if match is None:
        raise AssertionError(f"full SHA pin for {package} missing from requirements.txt")
    return match.group(1)


def _installed_vcs_commit(dist_name):
    dist = distribution(dist_name)
    payload = dist.read_text("direct_url.json")
    if not payload:
        raise AssertionError(f"{dist_name} has no direct_url.json (expected a git pin)")
    return json.loads(payload)["vcs_info"]["commit_id"]


def _application_python_files():
    skip_names = {"tests.py", "tests_deploy.py"}
    for root in (REPO_ROOT / "projects", REPO_ROOT / "example"):
        for path in root.rglob("*.py"):
            if path.name in skip_names or "migrations" in path.parts:
                continue
            yield path


class SeededTrustsDemoTests(TestCase):
    def setUp(self):
        self.data = seed_demo()
        self.users = self.data["users"]
        self.projects = self.data["projects"]

    def _titles(self, user):
        return list(readable_projects(user).values_list("title", flat=True))

    def test_seeded_users_groups_and_owned_objects(self):
        self.assertEqual(set(self.users), {"alice", "bob", "carol", "dave"})
        self.assertGreaterEqual(Project.objects.count(), 7)
        self.assertTrue(Trust.objects.filter(title="org:acme").exists())
        self.assertTrue(Trust.objects.filter(title="project:acme-playbook").exists())
        self.assertTrue(self.users["carol"].groups.filter(name="acme-staff").exists())
        handbook = self.projects["acme-handbook"]
        appendix = self.projects["acme-appendix"]
        self.assertEqual(handbook.trust_id, appendix.trust_id)
        self.assertEqual(handbook.trust.title, "org:acme")
        self.assertNotEqual(self.projects["acme-playbook"].trust_id, handbook.trust_id)

    def test_seeded_readable_titles(self):
        self.assertEqual(
            self._titles(self.users["alice"]),
            [
                "Acme Appendix",
                "Acme Handbook",
                "Acme Playbook",
                "Alice Private Notes",
                "Public Changelog",
                "Shared Roadmap",
            ],
        )
        self.assertEqual(
            self._titles(self.users["bob"]),
            ["Public Changelog", "Shared Roadmap"],
        )
        self.assertEqual(
            self._titles(self.users["carol"]),
            ["Acme Appendix", "Acme Handbook", "Acme Playbook", "Public Changelog"],
        )
        self.assertEqual(
            self._titles(self.users["dave"]),
            ["Dave Notes", "Public Changelog"],
        )

    def test_list_filtering_and_direct_access_agree(self):
        for user in self.users.values():
            listed = set(readable_projects(user).values_list("pk", flat=True))
            self.client.force_login(user)
            for project in Project.objects.all():
                allowed = user.has_perm("projects.read_project", project)
                self.assertEqual(
                    project.pk in listed,
                    allowed,
                    f"{user.username} list/direct disagree on {project.title}",
                )
                response = self.client.get(project.get_absolute_url())
                if allowed:
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, project.title)
                else:
                    self.assertEqual(response.status_code, 403)

    def test_list_view_uses_filtered_titles(self):
        self.client.force_login(self.users["dave"])
        response = self.client.get(reverse("project-list"))
        self.assertContains(response, "Dave Notes")
        self.assertContains(response, "Public Changelog")
        self.assertNotContains(response, "Alice Private Notes")
        self.assertNotContains(response, "Acme Handbook")
        self.assertNotContains(response, "Acme Appendix")
        self.assertNotContains(response, "Acme Playbook")
        self.assertNotContains(response, "Shared Roadmap")

    def test_create_object_grants_owner_read_and_change(self):
        self.client.force_login(self.users["bob"])
        response = self.client.post(
            reverse("project-create"),
            {"title": "Bob Scratchpad", "description": "created in test"},
        )
        project = Project.objects.get(slug="bob-scratchpad")
        self.assertRedirects(response, project.get_absolute_url())
        bob = User.objects.get(username="bob")
        self.assertTrue(bob.has_perm("projects.read_project", project))
        self.assertTrue(bob.has_perm("projects.change_project", project))
        self.assertFalse(self.users["alice"].has_perm("projects.read_project", project))

    def test_change_visibility_then_other_user_view(self):
        notes = self.projects["alice-private-notes"]
        dave = self.users["dave"]
        self.assertFalse(dave.has_perm("projects.read_project", notes))

        self.client.force_login(self.users["alice"])
        response = self.client.post(
            reverse("project-visibility", kwargs={"pk": notes.pk}),
            {"is_public": "on"},
        )
        self.assertRedirects(response, notes.get_absolute_url())
        notes = Project.objects.get(pk=notes.pk)
        self.assertTrue(is_public(notes))
        dave = User.objects.get(username="dave")
        self.assertTrue(dave.has_perm("projects.read_project", notes))
        self.assertIn(notes.pk, readable_projects(dave).values_list("pk", flat=True))

        self.client.post(
            reverse("project-visibility", kwargs={"pk": notes.pk}),
            {},
        )
        notes = Project.objects.get(pk=notes.pk)
        self.assertFalse(is_public(notes))
        dave = User.objects.get(username="dave")
        self.assertFalse(dave.has_perm("projects.read_project", notes))

    def test_grant_and_revoke_access(self):
        notes = self.projects["alice-private-notes"]
        dave = self.users["dave"]
        self.client.force_login(self.users["alice"])
        self.client.post(
            reverse("project-grant", kwargs={"pk": notes.pk}),
            {"user": dave.pk, "permission": READ},
        )
        dave = User.objects.get(username="dave")
        self.assertTrue(dave.has_perm("projects.read_project", notes))
        self.assertFalse(dave.has_perm("projects.change_project", notes))

        self.client.post(
            reverse("project-revoke", kwargs={"pk": notes.pk}),
            {"user": dave.pk},
        )
        dave = User.objects.get(username="dave")
        self.assertFalse(dave.has_perm("projects.read_project", notes))

    def test_prevent_unauthorized_edits(self):
        notes = self.projects["alice-private-notes"]
        self.client.force_login(self.users["bob"])
        self.assertEqual(
            self.client.get(reverse("project-edit", kwargs={"pk": notes.pk})).status_code,
            403,
        )
        response = self.client.post(
            reverse("project-edit", kwargs={"pk": notes.pk}),
            {"title": "Hacked", "description": "no"},
        )
        self.assertEqual(response.status_code, 403)
        notes.refresh_from_db()
        self.assertEqual(notes.title, "Alice Private Notes")

        self.assertEqual(
            self.client.post(
                reverse("project-grant", kwargs={"pk": notes.pk}),
                {"user": self.users["dave"].pk, "permission": CHANGE},
            ).status_code,
            403,
        )
        self.assertFalse(
            TrustUserPermission.objects.filter(
                trust=notes.trust,
                entity=self.users["dave"],
            ).exists()
        )

    def test_nonseeded_user_reads_public_via_group(self):
        changelog = self.projects["public-changelog"]
        notes = self.projects["alice-private-notes"]
        erin = User.objects.create_user("erin", "erin@example.com", "demo")
        self.assertTrue(erin.groups.filter(name=PUBLIC_GROUP_NAME).exists())
        self.assertTrue(erin.has_perm("projects.read_project", changelog))
        self.assertIn(changelog.pk, readable_projects(erin).values_list("pk", flat=True))
        self.assertFalse(erin.has_perm("projects.read_project", notes))
        self.assertNotIn(notes.pk, readable_projects(erin).values_list("pk", flat=True))
        self.client.force_login(erin)
        response = self.client.get(reverse("project-list"))
        self.assertContains(response, "Public Changelog")
        self.assertNotContains(response, "Alice Private Notes")

    def test_removing_public_readers_is_restored_without_seed(self):
        changelog = self.projects["public-changelog"]
        erin = User.objects.create_user("erin-resync", "erin-resync@example.com", "demo")
        erin.groups.remove(*erin.groups.filter(name=PUBLIC_GROUP_NAME))
        erin = User.objects.get(pk=erin.pk)
        self.assertTrue(erin.groups.filter(name=PUBLIC_GROUP_NAME).exists())
        self.assertTrue(erin.has_perm("projects.read_project", changelog))

    def test_inactive_user_list_matches_has_perm(self):
        bob = self.users["bob"]
        shared = self.projects["shared-roadmap"]
        self.assertTrue(bob.has_perm("projects.read_project", shared))
        self.assertTrue(readable_projects(bob).filter(pk=shared.pk).exists())

        bob.is_active = False
        bob.save()
        bob = User.objects.get(pk=bob.pk)
        self.assertFalse(bob.has_perm("projects.read_project", shared))
        self.assertFalse(readable_projects(bob).filter(pk=shared.pk).exists())
        self.assertFalse(editable_projects(bob).filter(pk=shared.pk).exists())


class PaginationAndQueryTests(TestCase):
    def setUp(self):
        seed_demo()
        self.alice = User.objects.get(username="alice")
        self.bob = User.objects.get(username="bob")

    def _owned(self, title):
        trust = Trust(
            settlor=self.alice,
            title=f"project:{title.lower().replace(' ', '-')}",
            trust=Trust.objects.get_root(),
        )
        trust.save()
        project = Project.objects.create(title=title, slug=title.lower().replace(" ", "-"), trust=trust)
        grant_user(project, self.alice, READ, CHANGE)
        return project

    def test_readable_projects_is_a_queryset_not_a_python_predicate(self):
        qs = readable_projects(self.alice)
        self.assertIsInstance(qs, QuerySet)
        sql = str(qs.query).lower()
        self.assertIn("trusts_trustuserpermission", sql)
        self.assertTrue(
            "trustgroup" in sql or "trusts_trust_groups" in sql,
            f"list filter must include TrustGroup intersection SQL, got: {sql}",
        )
        self.assertNotIn(":own", sql)
        page = Paginator(qs, 2).page(1)
        self.assertTrue(all(self.alice.has_perm("projects.read_project", obj) for obj in page))

    @override_settings(PROJECT_PAGE_SIZE=2)
    def test_permission_filter_precedes_pagination(self):
        extras = [self._owned(f"Pad {n}") for n in ("A", "B", "C", "D", "E")]
        grant_user(extras[2], self.bob, READ)
        grant_user(extras[4], self.bob, READ)
        bob = User.objects.get(username="bob")

        filtered = list(readable_projects(bob).filter(title__startswith="Pad"))
        self.assertEqual([p.title for p in filtered], ["Pad C", "Pad E"])

        page = Paginator(readable_projects(bob).filter(title__startswith="Pad"), 2).page(1)
        self.assertEqual([p.title for p in page], ["Pad C", "Pad E"])

        naive = Paginator(Project.objects.filter(title__startswith="Pad").order_by("title"), 2).page(1)
        leaked_or_empty = [p for p in naive if bob.has_perm("projects.read_project", p)]
        self.assertEqual(
            leaked_or_empty,
            [],
            "Paginating the unfiltered table first would hide Pad C/E from page 1.",
        )

        self.client.force_login(bob)
        with override_settings(PROJECT_PAGE_SIZE=2):
            response = self.client.get(reverse("project-list"))
        self.assertContains(response, "Pad C")
        self.assertNotContains(response, "Pad A")
        self.assertNotContains(response, "Pad B")

    def test_public_visibility_uses_group_grant_not_a_condition_code(self):
        changelog = Project.objects.get(slug="public-changelog")
        self.assertTrue(is_public(changelog))
        dave = User.objects.get(username="dave")
        self.assertTrue(dave.has_perm("projects.read_project", changelog))
        # Trust :own is a donated builder. Project does not register one;
        # public read is a group row, not a condition code.
        with self.assertRaises(AttributeError):
            dave.has_perm("projects.read_project:own", changelog)
        self.assertFalse(dave.has_perm("projects.change_project", changelog))


class ExistingUserBeforeSeedTests(TestCase):
    def test_user_created_before_seed_reads_public(self):
        frank = User.objects.create_user("frank", "frank@example.com", "demo")
        seed_demo()
        frank = User.objects.get(username="frank")
        changelog = Project.objects.get(slug="public-changelog")
        self.assertTrue(frank.groups.filter(name=PUBLIC_GROUP_NAME).exists())
        self.assertTrue(frank.has_perm("projects.read_project", changelog))
        self.assertIn(changelog.pk, readable_projects(frank).values_list("pk", flat=True))


class CreateCollisionTests(TransactionTestCase):
    def setUp(self):
        seed_demo()
        self.bob = User.objects.get(username="bob")

    def test_duplicate_title_resolves_slug_without_500(self):
        trusts_before = Trust.objects.count()
        projects_before = Project.objects.count()
        self.client.force_login(self.bob)
        response = self.client.post(
            reverse("project-create"),
            {"title": "Alice Private Notes", "description": "bob copy"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Trust.objects.count(), trusts_before + 1)
        self.assertEqual(Project.objects.count(), projects_before + 1)
        created = Project.objects.get(description="bob copy")
        self.assertEqual(created.title, "Alice Private Notes")
        self.assertNotEqual(created.slug, "alice-private-notes")
        self.assertTrue(created.slug.startswith("alice-private-notes"))
        self.assertRedirects(response, created.get_absolute_url())
        bob = User.objects.get(username="bob")
        self.assertTrue(bob.has_perm("projects.read_project", created))
        self.assertTrue(bob.has_perm("projects.change_project", created))

    def test_titles_that_normalize_to_the_same_slug(self):
        self.client.force_login(self.bob)
        response = self.client.post(
            reverse("project-create"),
            {"title": "alice  private notes", "description": "normalized"},
        )
        self.assertEqual(response.status_code, 302)
        created = Project.objects.get(description="normalized")
        self.assertEqual(created.slug, "alice-private-notes-2")
        self.assertNotEqual(created.slug, "alice-private-notes")

    def test_failed_create_rolls_back_trust(self):
        trusts_before = Trust.objects.count()
        projects_before = Project.objects.count()
        self.client.force_login(self.bob)
        with patch("projects.create.Project.save", side_effect=IntegrityError("forced")):
            response = self.client.post(
                reverse("project-create"),
                {"title": "Unique Enough Title", "description": "should roll back"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Could not create a unique project identifier")
        self.assertEqual(Trust.objects.count(), trusts_before)
        self.assertEqual(Project.objects.count(), projects_before)
        self.assertFalse(Project.objects.filter(title="Unique Enough Title").exists())

    def test_empty_slug_title_is_rejected(self):
        trusts_before = Trust.objects.count()
        self.client.force_login(self.bob)
        response = self.client.post(
            reverse("project-create"),
            {"title": "!!!", "description": "no slug"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "letters or numbers")
        self.assertEqual(Trust.objects.count(), trusts_before)


class PublicReadersFormAndAdminTests(TestCase):
    def setUp(self):
        seed_demo()
        self.changelog = Project.objects.get(slug="public-changelog")
        self.UserForm = modelform_factory(User, fields=("username", "groups"))

    def _assert_public_read(self, user):
        user = User.objects.get(pk=user.pk)
        self.assertTrue(user.groups.filter(name=PUBLIC_GROUP_NAME).exists())
        self.assertTrue(user.has_perm("projects.read_project", self.changelog))
        self.assertIn(self.changelog.pk, readable_projects(user).values_list("pk", flat=True))

    def test_modelform_create_without_selecting_public_group(self):
        form = self.UserForm(data={"username": "form-created-user", "groups": []})
        self.assertTrue(form.is_valid())
        user = form.save()
        self._assert_public_read(user)

    def test_modelform_edit_without_selecting_public_group(self):
        user = User.objects.create_user("form-edited-user", "fe@example.com", "demo")
        form = self.UserForm(
            data={"username": user.username, "groups": []},
            instance=user,
        )
        self.assertTrue(form.is_valid())
        form.save()
        self._assert_public_read(user)

    def test_useradmin_save_related_create_and_edit(self):
        from projects.admin import UserAdmin

        factory = RequestFactory()
        request = factory.post("/admin/auth/user/add/", {})
        request.user = User.objects.create_superuser("rootadmin", "root@example.com", "demo")
        admin = UserAdmin(User, AdminSite())

        form = self.UserForm(data={"username": "via-admin", "groups": []})
        self.assertTrue(form.is_valid())
        obj = form.save(commit=False)
        admin.save_model(request, obj, form, change=False)
        admin.save_related(request, form, [], change=False)
        self._assert_public_read(User.objects.get(username="via-admin"))

        obj = User.objects.get(username="via-admin")
        edit = self.UserForm(data={"username": obj.username, "groups": []}, instance=obj)
        self.assertTrue(edit.is_valid())
        obj = edit.save(commit=False)
        admin.save_model(request, obj, edit, change=True)
        admin.save_related(request, edit, [], change=True)
        self._assert_public_read(User.objects.get(username="via-admin"))

    def test_groups_set_empty_keeps_public_readers(self):
        user = User.objects.create_user("cleared-groups", "cg@example.com", "demo")
        user.groups.set([])
        self._assert_public_read(user)

    def test_group_admin_cannot_drop_public_readers_member(self):
        user = User.objects.create_user("group-admin-target", "gat@example.com", "demo")
        group = Group.objects.get(name=PUBLIC_GROUP_NAME)
        group.user_set.remove(user)
        self._assert_public_read(user)


class TrustGroupProjectSettingsTests(TestCase):
    def setUp(self):
        self.data = seed_demo()
        self.users = self.data["users"]
        self.projects = self.data["projects"]
        self.acme = Group.objects.get(name="acme-staff")
        self.handbook = self.projects["acme-handbook"]
        self.playbook = self.projects["acme-playbook"]
        self.appendix = self.projects["acme-appendix"]
        self.notes = self.projects["alice-private-notes"]
        self.shared = self.projects["shared-roadmap"]
        self.changelog = self.projects["public-changelog"]

    def _reload(self, user):
        return User.objects.get(pk=user.pk)

    def test_association_only_public_readers_is_not_public(self):
        dave = self.users["dave"]
        group = public_readers_group()
        self.notes.trust.groups.add(group)
        self.assertTrue(
            TrustGroup.objects.filter(trust=self.notes.trust, group=group).exists()
        )
        self.assertFalse(
            TrustGroupPermission.objects.filter(
                trustgroup__trust=self.notes.trust, trustgroup__group=group
            ).exists()
        )
        self.assertFalse(is_public(self.notes))
        dave = self._reload(dave)
        self.assertFalse(dave.has_perm("projects.read_project", self.notes))
        self.assertNotIn(self.notes.pk, readable_projects(dave).values_list("pk", flat=True))

        self.client.force_login(self.users["alice"])
        response = self.client.get(self.notes.get_absolute_url())
        self.assertContains(response, '<span class="badge">private</span>', html=True)
        self.assertNotContains(response, '<span class="badge">public</span>', html=True)
        self.assertContains(response, "Associated — grants nothing until local rights are enabled")
        self.assertNotContains(response, 'id="id_is_public" checked')
        self.assertFalse(response.context["is_public"])
        self.assertFalse(response.context["visibility_form"].initial["is_public"])

        public = public_readers_group()
        self.assertTrue(is_public(self.changelog))
        public.permissions.remove(project_permission(READ))
        self.assertTrue(
            TrustGroupPermission.objects.filter(
                trustgroup__trust=self.changelog.trust,
                trustgroup__group=public,
                permission=project_permission(READ),
            ).exists()
        )
        self.assertFalse(is_public(self.changelog))
        dave = self._reload(dave)
        self.assertFalse(dave.has_perm("projects.read_project", self.changelog))

    def test_same_team_change_on_a_read_on_b(self):
        carol = self.users["carol"]
        self.assertTrue(carol.has_perm("projects.read_project", self.handbook))
        self.assertFalse(carol.has_perm("projects.change_project", self.handbook))
        self.assertTrue(carol.has_perm("projects.read_project", self.playbook))
        self.assertTrue(carol.has_perm("projects.change_project", self.playbook))
        self.assertIn(self.playbook.pk, editable_projects(carol).values_list("pk", flat=True))
        self.assertNotIn(self.handbook.pk, editable_projects(carol).values_list("pk", flat=True))
        self.assertEqual(self.appendix.trust_id, self.handbook.trust_id)
        self.assertNotEqual(self.playbook.trust_id, self.handbook.trust_id)
        self.assertTrue(carol.has_perm("projects.read_project", self.appendix))
        self.assertFalse(carol.has_perm("projects.change_project", self.appendix))

    def test_detail_distinguishes_association_local_and_ceiling(self):
        self.client.force_login(self.users["alice"])
        handbook = self.client.get(self.handbook.get_absolute_url())
        self.assertContains(handbook, "Global ceiling")
        self.assertContains(handbook, "Local rights for this Trust")
        self.assertContains(handbook, "Acme Appendix")
        self.assertContains(handbook, "acme-staff")
        self.assertContains(handbook, "editor")
        self.assertContains(handbook, "does <strong>not</strong> grant access")
        self.assertNotContains(
            handbook, "Associated — grants nothing until local rights are enabled"
        )

        notes = self.client.get(self.notes.get_absolute_url())
        self.assertContains(notes, "Associate without granting access")
        self.assertContains(notes, "This only attaches the team to the Trust")

    def test_newly_associated_team_grants_nothing_until_local_rights(self):
        carol = self.users["carol"]
        self.assertFalse(carol.has_perm("projects.read_project", self.notes))
        self.client.force_login(self.users["alice"])
        response = self.client.post(
            reverse("project-associate-team", kwargs={"pk": self.notes.pk}),
            {"group": self.acme.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.notes.get_absolute_url())
        self.assertTrue(
            TrustGroup.objects.filter(trust=self.notes.trust, group=self.acme).exists()
        )
        self.assertFalse(
            TrustGroupPermission.objects.filter(
                trustgroup__trust=self.notes.trust, trustgroup__group=self.acme
            ).exists()
        )
        carol = self._reload(carol)
        self.assertFalse(carol.has_perm("projects.read_project", self.notes))
        self.assertNotIn(self.notes.pk, readable_projects(carol).values_list("pk", flat=True))

        detail = self.client.get(self.notes.get_absolute_url())
        self.assertContains(detail, "Associated — grants nothing until local rights are enabled")
        self.assertContains(detail, "No access granted until local rights are enabled")

        self.client.post(
            reverse("project-team-permissions", kwargs={"pk": self.notes.pk}),
            {"group": self.acme.pk, "permissions": [READ]},
        )
        carol = self._reload(carol)
        self.assertTrue(carol.has_perm("projects.read_project", self.notes))
        self.assertFalse(carol.has_perm("projects.change_project", self.notes))
        self.assertIn(self.notes.pk, readable_projects(carol).values_list("pk", flat=True))

    def test_removing_global_ceiling_revokes_everywhere_even_if_local_remains(self):
        local_change = TrustGroupPermission.objects.filter(
            trustgroup__trust=self.playbook.trust,
            trustgroup__group=self.acme,
            permission=project_permission(CHANGE),
        )
        self.assertTrue(local_change.exists())
        Role.objects.get(name="editor").groups.remove(self.acme)
        Role.objects.get(name="reader").groups.add(self.acme)
        carol = self._reload(self.users["carol"])
        self.assertTrue(local_change.exists())
        self.assertTrue(carol.has_perm("projects.read_project", self.playbook))
        self.assertFalse(carol.has_perm("projects.change_project", self.playbook))
        self.assertTrue(carol.has_perm("projects.read_project", self.handbook))
        self.assertFalse(carol.has_perm("projects.change_project", self.handbook))
        self.assertTrue(carol.has_perm("projects.read_project", self.appendix))
        self.assertFalse(carol.has_perm("projects.change_project", self.appendix))
        self.assertNotIn(self.playbook.pk, editable_projects(carol).values_list("pk", flat=True))
        self.assertTrue(carol.has_perm("projects.read_project", self.appendix))
        self.assertFalse(carol.has_perm("projects.change_project", self.appendix))

        self.client.force_login(self.users["alice"])
        detail = self.client.get(self.playbook.get_absolute_url())
        self.assertContains(detail, "outside the current ceiling")

    def test_removing_local_permission_does_not_affect_another_trust(self):
        self.client.force_login(self.users["alice"])
        self.client.post(
            reverse("project-team-permissions", kwargs={"pk": self.playbook.pk}),
            {"group": self.acme.pk, "permissions": [READ]},
        )
        carol = self._reload(self.users["carol"])
        self.assertTrue(carol.has_perm("projects.read_project", self.playbook))
        self.assertFalse(carol.has_perm("projects.change_project", self.playbook))
        self.assertTrue(carol.has_perm("projects.read_project", self.handbook))
        self.assertFalse(carol.has_perm("projects.change_project", self.handbook))
        self.assertTrue(
            TrustGroup.objects.filter(trust=self.playbook.trust, group=self.acme).exists()
        )
        self.assertTrue(
            TrustGroupPermission.objects.filter(
                trustgroup__trust=self.handbook.trust,
                trustgroup__group=self.acme,
                permission=project_permission(READ),
            ).exists()
        )
        self.assertTrue(carol.has_perm("projects.read_project", self.appendix))

    def test_shared_trust_local_rights_apply_to_all_projects_on_the_trust(self):
        self.assertEqual(self.appendix.trust_id, self.handbook.trust_id)
        carol = self.users["carol"]
        dave = self.users["dave"]
        self.client.force_login(self.users["alice"])
        handbook_page = self.client.get(self.handbook.get_absolute_url())
        self.assertContains(handbook_page, "this project's Trust")
        self.assertContains(handbook_page, "Remove team from this Trust")
        self.assertContains(handbook_page, "Acme Appendix")

        self.client.post(
            reverse("project-team-permissions", kwargs={"pk": self.handbook.pk}),
            {"group": self.acme.pk},
        )
        carol = self._reload(carol)
        self.assertFalse(carol.has_perm("projects.read_project", self.handbook))
        self.assertFalse(carol.has_perm("projects.read_project", self.appendix))
        self.assertTrue(carol.has_perm("projects.read_project", self.playbook))
        self.assertTrue(
            TrustGroup.objects.filter(trust=self.handbook.trust, group=self.acme).exists()
        )

        self.client.post(
            reverse("project-team-permissions", kwargs={"pk": self.appendix.pk}),
            {"group": self.acme.pk, "permissions": [READ]},
        )
        carol = self._reload(carol)
        self.assertTrue(carol.has_perm("projects.read_project", self.handbook))
        self.assertTrue(carol.has_perm("projects.read_project", self.appendix))

        self.client.post(
            reverse("project-visibility", kwargs={"pk": self.handbook.pk}),
            {"is_public": "on"},
        )
        self.assertTrue(is_public(self.handbook))
        self.assertTrue(is_public(self.appendix))
        dave = self._reload(dave)
        self.assertTrue(dave.has_perm("projects.read_project", self.handbook))
        self.assertTrue(dave.has_perm("projects.read_project", self.appendix))
        self.assertFalse(dave.has_perm("projects.read_project", self.playbook))

    def test_unauthorized_and_readonly_cannot_change_teams(self):
        self.client.force_login(self.users["bob"])
        for name in (
            "project-associate-team",
            "project-disassociate-team",
            "project-team-permissions",
        ):
            response = self.client.post(
                reverse(name, kwargs={"pk": self.notes.pk}),
                {"group": self.acme.pk, "permissions": [CHANGE]},
            )
            self.assertEqual(response.status_code, 403)
        self.assertFalse(
            TrustGroup.objects.filter(trust=self.notes.trust, group=self.acme).exists()
        )

        self.client.force_login(self.users["carol"])
        before = list(
            TrustGroupPermission.objects.filter(
                trustgroup__trust=self.handbook.trust, trustgroup__group=self.acme
            ).values_list("permission__codename", flat=True)
        )
        for name in (
            "project-associate-team",
            "project-disassociate-team",
            "project-team-permissions",
        ):
            response = self.client.post(
                reverse(name, kwargs={"pk": self.handbook.pk}),
                {"group": self.acme.pk, "permissions": [CHANGE]},
            )
            self.assertEqual(response.status_code, 403)
        after = list(
            TrustGroupPermission.objects.filter(
                trustgroup__trust=self.handbook.trust, trustgroup__group=self.acme
            ).values_list("permission__codename", flat=True)
        )
        self.assertEqual(sorted(before), sorted(after))
        self.assertEqual(
            self.client.get(reverse("project-edit", kwargs={"pk": self.handbook.pk})).status_code,
            403,
        )

        self.client.force_login(self.users["bob"])
        self.assertEqual(
            self.client.post(
                reverse("project-team-permissions", kwargs={"pk": self.shared.pk}),
                {"group": self.acme.pk, "permissions": [READ]},
            ).status_code,
            403,
        )

    def test_unknown_and_cross_project_ids_fail_closed(self):
        self.client.force_login(self.users["alice"])
        handbook_local = set(
            TrustGroupPermission.objects.filter(
                trustgroup__trust=self.handbook.trust, trustgroup__group=self.acme
            ).values_list("permission_id", flat=True)
        )
        notes_groups_before = set(
            TrustGroup.objects.filter(trust=self.notes.trust).values_list("group_id", flat=True)
        )

        self.client.post(
            reverse("project-team-permissions", kwargs={"pk": self.notes.pk}),
            {"group": self.acme.pk, "permissions": [READ]},
        )
        self.assertFalse(
            TrustGroup.objects.filter(trust=self.notes.trust, group=self.acme).exists()
        )

        self.client.post(
            reverse("project-disassociate-team", kwargs={"pk": self.notes.pk}),
            {"group": self.acme.pk},
        )
        self.assertTrue(
            TrustGroup.objects.filter(trust=self.handbook.trust, group=self.acme).exists()
        )

        self.client.post(
            reverse("project-associate-team", kwargs={"pk": self.notes.pk}),
            {"group": 999999},
        )
        self.client.post(
            reverse("project-team-permissions", kwargs={"pk": self.playbook.pk}),
            {"group": 999999, "permissions": [READ]},
        )
        self.client.post(
            reverse("project-disassociate-team", kwargs={"pk": self.playbook.pk}),
            {"group": 999999},
        )

        delete_perm = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(Project),
            codename="delete_project",
        )
        self.client.post(
            reverse("project-team-permissions", kwargs={"pk": self.playbook.pk}),
            {"group": self.acme.pk, "permissions": [READ, CHANGE, str(delete_perm.pk)]},
        )
        self.client.post(
            reverse("project-team-permissions", kwargs={"pk": self.playbook.pk}),
            {"group": self.acme.pk, "permissions": ["not-a-permission"]},
        )
        public = Group.objects.get(name=PUBLIC_GROUP_NAME)
        self.client.post(
            reverse("project-associate-team", kwargs={"pk": self.notes.pk}),
            {"group": public.pk},
        )
        self.client.post(
            reverse("project-disassociate-team", kwargs={"pk": self.changelog.pk}),
            {"group": public.pk},
        )
        self.assertTrue(is_public(self.changelog))

        self.assertEqual(
            handbook_local,
            set(
                TrustGroupPermission.objects.filter(
                    trustgroup__trust=self.handbook.trust, trustgroup__group=self.acme
                ).values_list("permission_id", flat=True)
            ),
        )
        self.assertEqual(
            notes_groups_before,
            set(TrustGroup.objects.filter(trust=self.notes.trust).values_list("group_id", flat=True)),
        )
        self.assertEqual(
            set(
                TrustGroupPermission.objects.filter(
                    trustgroup__trust=self.playbook.trust, trustgroup__group=self.acme
                ).values_list("permission__codename", flat=True)
            ),
            {READ, CHANGE},
        )
        carol = self._reload(self.users["carol"])
        self.assertTrue(carol.has_perm("projects.change_project", self.playbook))

    def test_out_of_ceiling_submit_rejected_without_mutation(self):
        contractors, _ = Group.objects.get_or_create(name="contractors")
        contractors.permissions.add(project_permission(READ))
        contractors.user_set.add(self.users["bob"])
        self.client.force_login(self.users["alice"])
        self.client.post(
            reverse("project-associate-team", kwargs={"pk": self.notes.pk}),
            {"group": contractors.pk},
        )
        self.client.post(
            reverse("project-team-permissions", kwargs={"pk": self.notes.pk}),
            {"group": contractors.pk, "permissions": [READ, CHANGE]},
        )
        tg = TrustGroup.objects.get(trust=self.notes.trust, group=contractors)
        self.assertEqual(set(tg.permissions.values_list("codename", flat=True)), set())
        bob = self._reload(self.users["bob"])
        self.assertFalse(bob.has_perm("projects.read_project", self.notes))
        self.assertFalse(bob.has_perm("projects.change_project", self.notes))

        self.client.post(
            reverse("project-team-permissions", kwargs={"pk": self.notes.pk}),
            {"group": contractors.pk, "permissions": [READ]},
        )
        bob = self._reload(self.users["bob"])
        self.assertTrue(bob.has_perm("projects.read_project", self.notes))

        before = set(tg.permissions.values_list("pk", flat=True))
        self.client.post(
            reverse("project-team-permissions", kwargs={"pk": self.notes.pk}),
            {"group": contractors.pk, "permissions": [READ, CHANGE]},
        )
        tg.refresh_from_db()
        self.assertEqual(before, set(tg.permissions.values_list("pk", flat=True)))
        bob = self._reload(self.users["bob"])
        self.assertTrue(bob.has_perm("projects.read_project", self.notes))
        self.assertFalse(bob.has_perm("projects.change_project", self.notes))

    def test_seeded_public_readers_and_acme_keep_intended_access(self):
        dave = self.users["dave"]
        self.assertTrue(dave.has_perm("projects.read_project", self.changelog))
        self.assertFalse(dave.has_perm("projects.change_project", self.changelog))
        self.assertTrue(
            TrustGroupPermission.objects.filter(
                trustgroup__trust=self.changelog.trust,
                trustgroup__group__name=PUBLIC_GROUP_NAME,
                permission=project_permission(READ),
            ).exists()
        )
        carol = self.users["carol"]
        self.assertEqual(
            list(readable_projects(carol).values_list("title", flat=True)),
            ["Acme Appendix", "Acme Handbook", "Acme Playbook", "Public Changelog"],
        )


class ZeroInstallAndCheckTests(SimpleTestCase):
    """Zero install identity: ZeroConfig, Zero backend, builder :own, clean check."""

    def test_installed_apps_use_zero_config_not_bare_trusts(self):
        self.assertIn("trusts.zero.apps.ZeroConfig", settings.INSTALLED_APPS)
        self.assertNotIn("trusts", settings.INSTALLED_APPS)

    def test_backend_is_zero_not_core_path(self):
        self.assertIn(
            "trusts.zero.backends.TrustModelBackend",
            settings.AUTHENTICATION_BACKENDS,
        )
        self.assertNotIn(
            "trusts.backends.TrustModelBackend",
            settings.AUTHENTICATION_BACKENDS,
        )

    def test_project_is_registered_as_zero_content(self):
        handles = zero_config().configured_handles()
        self.assertTrue(any_plan_records(handles, Project))

    def test_legacy_callback_setting_is_unset(self):
        self.assertFalse(
            getattr(settings, "TRUSTS_ALLOW_LEGACY_PERMISSION_CALLBACKS", False)
        )

    def test_paired_core_and_zero_pins_are_installed(self):
        self.assertEqual(_pin_sha_from_requirements("django-trusts"), CORE_PIN_SHA)
        self.assertEqual(_pin_sha_from_requirements("django-trusts-zero"), ZERO_PIN_SHA)
        pyproject = (REPO_ROOT / "pyproject.toml").read_text()
        self.assertIn(CORE_PIN_SHA, pyproject)
        self.assertIn(ZERO_PIN_SHA, pyproject)
        self.assertEqual(_installed_vcs_commit("django-trusts"), CORE_PIN_SHA)
        self.assertEqual(_installed_vcs_commit("django-trusts-zero"), ZERO_PIN_SHA)

    def test_application_modules_do_not_import_core_condition_nodes(self):
        imported = []
        for path in _application_python_files():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in {
                    "trusts.conditions",
                    "django_trusts",
                }:
                    for alias in node.names or []:
                        if alias.name in FORBIDDEN_CONDITION_NODES or alias.name == "*":
                            imported.append(f"{path.relative_to(REPO_ROOT)}:{alias.name}")
        self.assertEqual(imported, [])

    def test_system_checks_have_no_trusts_condition_errors(self):
        messages = django_checks.run_checks()
        condition_ids = {m.id for m in messages} & {
            "trusts.E001",
            "trusts.E007",
        }
        self.assertEqual(condition_ids, set())
        errors = [m for m in messages if m.level >= django_checks.ERROR]
        self.assertEqual(errors, [])


class TrustOwnPublicOutcomeTests(TestCase):
    """Trust:own is donated at startup; prove it through public outcomes only."""

    @classmethod
    def setUpTestData(cls):
        cls.data = seed_demo()
        cls.alice = cls.data["users"]["alice"]
        cls.bob = cls.data["users"]["bob"]
        cls.dave = cls.data["users"]["dave"]
        cls.acme = cls.data["trusts"]["acme"]
        cls.dave_notes = cls.data["trusts"]["dave_notes"]
        cls.notes = cls.data["projects"]["alice-private-notes"]

    def test_startup_reentry_and_first_party_builder_are_zero_sql(self):
        handle = zero_config().configured_backend(CANONICAL_BACKEND_PATH)
        with self.assertNumQueries(0):
            donate_installed_permission_conditions(handle)

        isolated = TrustsRegistry()
        with self.assertNumQueries(0):
            donate_content_permission_conditions(isolated, Trust)
        self.assertTrue(
            isolated.evaluate_permission_condition(
                Trust, "own", self.alice, "trusts.change_trust", self.acme
            )
        )
        self.assertFalse(
            isolated.evaluate_permission_condition(
                Trust, "own", self.dave, "trusts.change_trust", self.acme
            )
        )

    def test_trust_own_is_registered_at_startup_and_project_has_none(self):
        alice = User.objects.get(pk=self.alice.pk)
        # Registered: evaluates fail-closed without a Trust grant, does not raise.
        self.assertFalse(alice.has_perm("trusts.change_trust:own", self.acme))
        with self.assertRaises(AttributeError):
            alice.has_perm("projects.read_project:own", self.notes)
        with self.assertRaises(AttributeError):
            list(Project.objects.permitted("read_project:own", alice))

    def test_trust_own_object_and_listing_agree(self):
        change_trust = Permission.objects.get(
            content_type=ContentType.objects.get_for_model(Trust),
            codename="change_trust",
        )
        root = Trust.objects.get_root()
        for user in (self.alice, self.dave):
            TrustUserPermission.objects.get_or_create(
                trust=root,
                entity=user,
                permission=change_trust,
            )

        alice = User.objects.get(pk=self.alice.pk)
        dave = User.objects.get(pk=self.dave.pk)
        self.assertTrue(alice.has_perm("trusts.change_trust:own", self.acme))
        self.assertFalse(dave.has_perm("trusts.change_trust:own", self.acme))
        self.assertTrue(dave.has_perm("trusts.change_trust:own", self.dave_notes))
        self.assertFalse(alice.has_perm("trusts.change_trust:own", self.dave_notes))

        alice_listed = set(Trust.objects.permitted("change:own", alice))
        dave_listed = set(Trust.objects.permitted("change:own", dave))
        alice_evaluated = {
            row
            for row in Trust.objects.all()
            if alice.has_perm("trusts.change_trust:own", row)
        }
        dave_evaluated = {
            row
            for row in Trust.objects.all()
            if dave.has_perm("trusts.change_trust:own", row)
        }
        self.assertEqual(alice_listed, alice_evaluated)
        self.assertEqual(dave_listed, dave_evaluated)
        self.assertIn(self.acme, alice_listed)
        self.assertNotIn(self.acme, dave_listed)
        self.assertIn(self.dave_notes, dave_listed)
        self.assertNotIn(self.dave_notes, alice_listed)

        bob = User.objects.get(pk=self.bob.pk)
        self.assertFalse(bob.has_perm("trusts.change_trust:own", self.acme))
        self.assertNotIn(self.acme, Trust.objects.permitted("change:own", bob))

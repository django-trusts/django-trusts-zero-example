from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Permission, User
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render

from trusts.zero.authorization import (
    AuthorizationDenied,
    associate_group_with_trust,
    disassociate_group_from_trust,
    set_trust_group_permissions,
)
from trusts.zero.decorators import permission_required, K

from .create import create_owned_project
from .forms import AssociateTeamForm, GrantForm, ProjectForm, VisibilityForm
from .grants import (
    CHANGE,
    LOCAL_GRANTABLE,
    PUBLIC_GROUP_NAME,
    READ,
    grant_user,
    is_public,
    project_content_type,
    project_permission,
    revoke_user,
    set_public,
    team_setting_rows,
    trustee_rows,
)
from .models import Project
from .query import editable_projects, readable_projects


def _page_size():
    return getattr(settings, "PROJECT_PAGE_SIZE", 3)


@login_required
def project_list(request):
    # Filter first, then paginate. Do not slice the unfiltered table.
    qs = readable_projects(request.user)
    page = Paginator(qs, _page_size()).get_page(request.GET.get("page"))
    return render(
        request,
        "projects/project_list.html",
        {
            "page": page,
            "can_edit_ids": set(editable_projects(request.user).values_list("pk", flat=True)),
            "demo_users": ("alice", "bob", "carol", "dave"),
        },
    )


@login_required
def project_create(request):
    form = ProjectForm(request.POST or None)
    if form.is_valid():
        try:
            project = create_owned_project(
                request.user,
                form.cleaned_data["title"],
                form.cleaned_data.get("description", ""),
            )
        except IntegrityError:
            form.add_error(
                "title",
                "Could not create a unique project identifier. Try a different title.",
            )
        else:
            messages.success(request, f"Created {project.title}. You have read and change.")
            return redirect(project)
    return render(request, "projects/project_form.html", {"form": form, "mode": "create"})


def _detail_context(request, project, can_change):
    return {
        "project": project,
        "can_change": can_change,
        "is_public": is_public(project),
        "trustees": trustee_rows(project),
        "teams": team_setting_rows(project),
        "trust_projects": Project.objects.filter(trust=project.trust).order_by("title", "pk"),
        "grant_form": GrantForm() if can_change else None,
        "visibility_form": VisibilityForm(initial={"is_public": is_public(project)})
        if can_change
        else None,
        "associate_form": AssociateTeamForm(project=project) if can_change else None,
    }


@login_required
@permission_required("projects.read_project", pk=K("pk"))
def project_detail(request, pk):
    project = get_object_or_404(Project, pk=pk)
    can_change = request.user.has_perm("projects.change_project", project)
    return render(request, "projects/project_detail.html", _detail_context(request, project, can_change))


@login_required
@permission_required("projects.change_project", pk=K("pk"))
def project_edit(request, pk):
    project = get_object_or_404(Project, pk=pk)
    form = ProjectForm(request.POST or None, instance=project)
    if form.is_valid():
        form.save()
        messages.success(request, "Project updated.")
        return redirect(project)
    return render(
        request,
        "projects/project_form.html",
        {"form": form, "mode": "edit", "project": project},
    )


@login_required
@permission_required("projects.change_project", pk=K("pk"))
def project_visibility(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method != "POST":
        return redirect(project)
    form = VisibilityForm(request.POST)
    if form.is_valid():
        set_public(project, form.cleaned_data["is_public"])
        messages.success(
            request,
            "Visibility is now public." if is_public(project) else "Visibility is now private.",
        )
    return redirect(project)


@login_required
@permission_required("projects.change_project", pk=K("pk"))
def project_grant(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method != "POST":
        return redirect(project)
    form = GrantForm(request.POST)
    if form.is_valid():
        user = form.cleaned_data["user"]
        perm = form.cleaned_data["permission"]
        if perm == CHANGE:
            grant_user(project, user, READ, CHANGE)
        else:
            revoke_user(project, user, CHANGE)
            grant_user(project, user, READ)
        messages.success(request, f"Granted {perm} to {user.username}.")
    else:
        messages.error(request, "Could not grant access.")
    return redirect(project)


@login_required
@permission_required("projects.change_project", pk=K("pk"))
def project_revoke(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method != "POST":
        return redirect(project)
    user = get_object_or_404(User, pk=request.POST.get("user"))
    revoke_user(project, user)
    messages.success(request, f"Revoked access for {user.username}.")
    return redirect(project)


@login_required
@permission_required("projects.change_project", pk=K("pk"))
def project_associate_team(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method != "POST":
        return redirect(project)
    form = AssociateTeamForm(request.POST, project=project)
    if not form.is_valid():
        messages.error(request, "Could not associate team. No changes were saved.")
        return redirect(project)
    group = form.cleaned_data["group"]
    if group.name == PUBLIC_GROUP_NAME:
        messages.error(request, "Public visibility is managed separately. No changes were saved.")
        return redirect(project)
    try:
        associate_group_with_trust(request.user, project, group.pk)
    except AuthorizationDenied:
        messages.error(request, "Could not associate team. No changes were saved.")
        return redirect(project)
    messages.success(
        request,
        f"Associated {group.name} with this Trust. No access granted until local rights are enabled.",
    )
    return redirect(project)


@login_required
@permission_required("projects.change_project", pk=K("pk"))
def project_disassociate_team(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method != "POST":
        return redirect(project)
    group_id = request.POST.get("group")
    if _posted_group_is_public_readers(project, group_id):
        messages.error(
            request,
            "Public visibility is managed separately. No changes were saved.",
        )
        return redirect(project)
    try:
        group = disassociate_group_from_trust(request.user, project, group_id)
    except AuthorizationDenied:
        messages.error(request, "Could not remove team. No changes were saved.")
        return redirect(project)
    messages.success(request, f"Removed {group.name} from this Trust.")
    return redirect(project)


@login_required
@permission_required("projects.change_project", pk=K("pk"))
def project_team_permissions(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method != "POST":
        return redirect(project)
    group_id = request.POST.get("group")
    if _posted_group_is_public_readers(project, group_id):
        messages.error(
            request,
            "Public visibility is managed separately. No changes were saved.",
        )
        return redirect(project)
    submitted = request.POST.getlist("permissions")
    try:
        resolved = [_resolve_local_permission(item) for item in submitted]
    except AuthorizationDenied:
        messages.error(
            request,
            "Submitted permissions are outside the locally grantable set. No changes were saved.",
        )
        return redirect(project)
    try:
        set_trust_group_permissions(request.user, project, group_id, resolved)
    except AuthorizationDenied:
        messages.error(
            request,
            "Could not update local rights. Permissions outside the team's "
            "global ceiling are rejected. No changes were saved.",
        )
        return redirect(project)
    messages.success(
        request,
        "Updated local team rights for this Trust. They apply to every project using it.",
    )
    return redirect(project)


def _posted_group_is_public_readers(project, group_id):
    return project.trust.groups.filter(pk=group_id, name=PUBLIC_GROUP_NAME).exists()


def _resolve_local_permission(item):
    """Accept only this app's read/change codes or those permission PKs."""
    if item in LOCAL_GRANTABLE:
        return project_permission(item)
    try:
        perm = Permission.objects.get(pk=item)
    except (Permission.DoesNotExist, ValueError, TypeError):
        raise AuthorizationDenied("Submitted entity is outside the authorized scope.")
    if perm.codename not in LOCAL_GRANTABLE or perm.content_type_id != project_content_type().pk:
        raise AuthorizationDenied("Submitted entity is outside the authorized scope.")
    return perm

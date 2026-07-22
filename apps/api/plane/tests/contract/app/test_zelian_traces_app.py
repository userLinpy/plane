# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Contract tests — module api/provisioning-zelian (US-06 : traiter les traces).

Endpoints server-to-server (secret ``X-Zelian-Provisioning-Key``) :
  - ``POST /api/zelian/traces/inventory/`` : inventaire des contributions (lecture) ;
  - ``POST /api/zelian/traces/treatment/`` : légation, purge sélective/totale DÉFINITIVE,
    marquage « Profil supprimé » ou suppression du compte.

Couvre les critères de spec-fonctionnel.md : inventaire, purge définitive (RM-06, via
``all_objects``), landmine ``IssueActivity`` (DO_NOTHING), projet partagé préservé (cas 8),
suppression de compte + recoche vierge, légation (RM-08 / cas 7), tombstone (RM-07),
companion dé-tombstone, comptes protégés (RM-09), habilitation (RM-13), silence (RM-11),
idempotence (RM-10), effacement S3.
"""

import uuid

import pytest
from crum import impersonate
from django.core import mail
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import (
    Cycle,
    FileAsset,
    Issue,
    IssueActivity,
    IssueComment,
    IssueView,
    Page,
    Project,
    ProjectMember,
    ProjectPage,
    User,
    Workspace,
    WorkspaceMember,
)

INVENTORY_URL = "/api/zelian/traces/inventory/"
TREATMENT_URL = "/api/zelian/traces/treatment/"
PROVISION_URL = "/api/zelian/provisioning/"
SECRET = "test-provisioning-secret"


@pytest.fixture
def configured(monkeypatch):
    """Secret de provisioning configuré (fallback env de get_configuration_value)."""
    monkeypatch.setenv("ZELIAN_PROVISIONING_SECRET", SECRET)
    return SECRET


# --------------------------------------------------------------------------- #
# Helpers requête                                                             #
# --------------------------------------------------------------------------- #
def _client(key=SECRET):
    client = APIClient()
    if key is not None:
        client.credentials(HTTP_X_ZELIAN_PROVISIONING_KEY=key)
    return client


def _inv(email, *, slug="test-workspace", key=SECRET):
    return _client(key).post(
        INVENTORY_URL, {"workspace_slug": slug, "email": email}, format="json"
    )


def _treat(payload, *, slug="test-workspace", key=SECRET):
    body = {"workspace_slug": slug}
    body.update(payload)
    return _client(key).post(TREATMENT_URL, body, format="json")


def _provision(members, *, slug="test-workspace", key=SECRET):
    return _client(key).post(
        PROVISION_URL,
        {"workspace_slug": slug, "members": members},
        format="json",
    )


# --------------------------------------------------------------------------- #
# Helpers fabrique                                                            #
# --------------------------------------------------------------------------- #
def _make_user(email, **kwargs):
    user = User.objects.create(
        email=email, username=uuid.uuid4().hex, first_name=email.split("@")[0], **kwargs
    )
    user.set_password("x")
    user.save()
    return user


def _member(workspace, user, role=15, is_active=True):
    return WorkspaceMember.objects.create(
        workspace=workspace, member=user, role=role, is_active=is_active
    )


def _project(workspace, owner, name="Alpha"):
    with impersonate(owner):  # crum -> created_by = owner
        return Project.objects.create(
            name=name, identifier=uuid.uuid4().hex[:10].upper(), workspace=workspace
        )


def _issue(workspace, project, author, name="WI"):
    with impersonate(author):  # crum -> created_by = author
        return Issue.objects.create(name=name, project=project, workspace=workspace)


def _comment(workspace, project, issue, author):
    return IssueComment.objects.create(
        workspace=workspace,
        project=project,
        issue=issue,
        actor=author,
        comment_html="<p>hi</p>",
    )


def _activity(workspace, project, issue=None, comment=None):
    return IssueActivity.objects.create(
        workspace=workspace,
        project=project,
        issue=issue,
        issue_comment=comment,
        verb="created",
    )


def _page(workspace, owner, project=None):
    page = Page.objects.create(name="Pg", workspace=workspace, owned_by=owner)
    if project is not None:
        ProjectPage.objects.create(workspace=workspace, project=project, page=page)
    return page


def _asset(workspace, user, project=None, issue=None):
    return FileAsset.objects.create(
        workspace=workspace,
        project=project,
        issue=issue,
        user=user,
        entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        asset=f"{workspace.id}/{uuid.uuid4().hex}-file.png",
        size=10,
        is_uploaded=True,
    )


@pytest.mark.contract
@pytest.mark.django_db
class TestZelianTracesInventory:
    def test_unknown_email_is_noop(self, workspace, configured):
        resp = _inv("ghost@zelian.fr")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["status"] == "unknown"  # cas 6

    def test_counts_contributions(self, workspace, create_user, configured):
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        project = _project(workspace, alice)
        issue = _issue(workspace, project, alice)
        _comment(workspace, project, issue, alice)
        _page(workspace, alice)
        _asset(workspace, alice, project=project, issue=issue)

        resp = _inv("alice@zelian.fr")
        assert resp.status_code == status.HTTP_200_OK
        totals = resp.data["totals"]
        assert totals["projects_created"] == 1
        assert totals["issues_created"] == 1
        assert totals["comments"] == 1
        assert totals["pages"] == 1
        assert totals["attachments"] == 1

    def test_flags_shared_project(self, workspace, create_user, configured):
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        project = _project(workspace, alice)
        # create_user (owner) est membre actif + a une contribution -> projet partagé
        ProjectMember.objects.create(
            workspace=workspace, project=project, member=create_user, role=20, is_active=True
        )
        _issue(workspace, project, create_user)

        resp = _inv("alice@zelian.fr")
        proj = resp.data["projects"][0]
        assert proj["shared"] is True
        assert proj["recommend_legation"] is True

    def test_bot_is_protected(self, workspace, create_bot_user, configured):
        resp = _inv(create_bot_user.email)
        assert resp.data["status"] == "protected"  # RM-09

    def test_missing_secret_is_forbidden(self, workspace, configured):
        resp = _inv("x@zelian.fr", key=None)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_wrong_secret_is_forbidden(self, workspace, configured):
        resp = _inv("x@zelian.fr", key="wrong")
        assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.contract
@pytest.mark.django_db
class TestZelianTracesTombstone:
    def test_keep_and_mark_tombstones_and_deactivates(self, workspace, configured):
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        project = _project(workspace, alice)
        issue = _issue(workspace, project, alice)

        resp = _treat({"email": "alice@zelian.fr", "account": "mask", "keep_name": True})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["account"] == "masked"

        alice.refresh_from_db()
        assert alice.masked_at is not None  # RM-07
        assert alice.display_name.startswith("Profil supprimé")
        assert alice.avatar == ""
        assert alice.is_active is False
        assert alice.last_logout_time is not None  # gate GHSA
        # traces conservées : l'issue existe toujours
        assert Issue.all_objects.filter(pk=issue.pk).exists()
        assert len(mail.outbox) == 0  # RM-11

    def test_keep_and_mark_is_idempotent(self, workspace, configured):
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        _treat({"email": "alice@zelian.fr", "account": "mask"})
        alice.refresh_from_db()
        first_masked_at = alice.masked_at

        resp = _treat({"email": "alice@zelian.fr", "account": "mask"})
        assert resp.data["account"] == "unchanged"  # RM-10
        alice.refresh_from_db()
        assert alice.masked_at == first_masked_at

    def test_reprovision_unmasks(self, workspace, configured):
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        _treat({"email": "alice@zelian.fr", "account": "mask"})

        # companion : la recoche lève le tombstone et restaure un compte propre
        resp = _provision([{"email": "alice@zelian.fr", "name": "Alice Martin"}])
        assert resp.status_code == status.HTTP_200_OK
        alice.refresh_from_db()
        assert alice.masked_at is None
        assert alice.last_logout_time is None
        assert alice.is_active is True
        assert alice.first_name == "Alice"


@pytest.mark.contract
@pytest.mark.django_db
class TestZelianTracesPurge:
    def test_category_purge_is_definitive(self, workspace, configured):
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        project = _project(workspace, alice)
        issue = _issue(workspace, project, alice)
        _activity(workspace, project, issue=issue)  # landmine IssueActivity (DO_NOTHING)

        resp = _treat({"email": "alice@zelian.fr", "account": "mask", "categories": {"issues": True}})
        assert resp.status_code == status.HTTP_200_OK
        # RM-06 : définitif -> absent même de all_objects (pas un simple soft-delete)
        assert Issue.all_objects.filter(pk=issue.pk).exists() is False
        assert IssueActivity.all_objects.filter(issue_id=issue.pk).exists() is False

    def test_purge_issue_with_comment_activity_no_integrity_error(self, workspace, configured):
        alice = _make_user("alice@zelian.fr")
        bob = _make_user("bob@zelian.fr")
        _member(workspace, alice)
        _member(workspace, bob)
        project = _project(workspace, alice)
        issue = _issue(workspace, project, alice)
        comment = _comment(workspace, project, issue, bob)  # commentaire d'autrui sur l'issue
        _activity(workspace, project, comment=comment)  # IssueActivity -> issue_comment (DO_NOTHING)

        resp = _treat({"email": "alice@zelian.fr", "account": "mask", "categories": {"issues": True}})
        assert resp.status_code == status.HTTP_200_OK  # pas d'IntegrityError au COMMIT
        assert Issue.all_objects.filter(pk=issue.pk).exists() is False
        assert IssueComment.all_objects.filter(pk=comment.pk).exists() is False

    def test_purge_shared_project_preserves_others(self, workspace, create_user, configured):
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        project = _project(workspace, alice)
        ProjectMember.objects.create(
            workspace=workspace, project=project, member=create_user, role=20, is_active=True
        )
        alice_issue = _issue(workspace, project, alice)
        boss_issue = _issue(workspace, project, create_user)

        resp = _treat({"email": "alice@zelian.fr", "account": "mask",
                       "projects": [{"project_id": str(project.id), "action": "purge"}]})
        assert resp.status_code == status.HTTP_200_OK
        op = resp.data["operations"][0]
        assert op["result"] == "purged_own_only"  # cas 8 : projet partagé pas détruit en entier
        assert Issue.all_objects.filter(pk=alice_issue.pk).exists() is False  # items d'alice purgés
        assert Issue.all_objects.filter(pk=boss_issue.pk).exists() is True  # contenu d'autrui intact
        assert Project.all_objects.filter(pk=project.pk).exists() is True  # projet préservé
        assert Workspace.objects.filter(pk=workspace.pk).exists() is True

    def test_total_delete_removes_account_and_content(self, workspace, create_user, configured):
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        project = _project(workspace, create_user)  # projet d'autrui
        issue = _issue(workspace, project, alice)  # contribution d'alice dedans

        resp = _treat({"email": "alice@zelian.fr", "account": "delete"})
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["account"] == "deleted"
        # RM-06 : plus aucune trace de la personne
        assert User.objects.filter(pk=alice.pk).exists() is False
        assert WorkspaceMember.objects.filter(member_id=alice.pk).exists() is False
        assert Issue.all_objects.filter(pk=issue.pk).exists() is False
        # projet d'autrui préservé
        assert Project.all_objects.filter(pk=project.pk).exists() is True

    def test_reprovision_after_total_delete_is_blank(self, workspace, configured):
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        old_pk = alice.pk
        _treat({"email": "alice@zelian.fr", "account": "delete"})

        resp = _provision([{"email": "alice@zelian.fr"}])
        assert resp.data["results"][0]["status"] == "created"
        new = User.objects.get(email__iexact="alice@zelian.fr")
        assert new.pk != old_pk  # membre vierge (nouvelle ligne)

    def test_total_delete_refused_when_owns_workspace(self, workspace, create_user, configured):
        # create_user est l'owner du workspace (fixture) -> suppression refusée
        resp = _treat({"email": create_user.email, "account": "delete"})
        assert resp.data["status"] == "rejected"
        assert resp.data["reason"] == "owns_workspace"
        assert User.objects.filter(pk=create_user.pk).exists() is True
        assert Workspace.objects.filter(pk=workspace.pk).exists() is True

    def test_total_delete_neutralizes_shared_containers(self, workspace, create_user, configured):
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        # projet d'autrui dont alice est (par accident) lead + owner d'un cycle/vue
        project = _project(workspace, create_user)
        Project.all_objects.filter(pk=project.pk).update(project_lead=alice, default_assignee=alice)
        cycle = Cycle.objects.create(name="C1", project=project, workspace=workspace, owned_by=alice)
        view = IssueView.objects.create(name="V1", workspace=workspace, project=project, owned_by=alice)

        resp = _treat({"email": "alice@zelian.fr", "account": "delete"})
        assert resp.data["account"] == "deleted"
        project.refresh_from_db()
        assert project.project_lead_id is None  # neutralisé
        assert project.default_assignee_id is None
        # conteneurs non-nullables réattribués à l'owner -> survivent
        assert Cycle.all_objects.filter(pk=cycle.pk, owned_by=create_user).exists()
        assert IssueView.all_objects.filter(pk=view.pk, owned_by=create_user).exists()
        assert Project.all_objects.filter(pk=project.pk).exists()


@pytest.mark.contract
@pytest.mark.django_db
class TestZelianTracesLegation:
    def test_legation_transfers_to_active_successor(self, workspace, configured):
        alice = _make_user("alice@zelian.fr")
        bob = _make_user("bob@zelian.fr")
        _member(workspace, alice)
        _member(workspace, bob)  # repreneur = membre actif
        project = _project(workspace, alice)
        issue = _issue(workspace, project, alice)

        resp = _treat({"email": "alice@zelian.fr", "account": "mask",
                       "projects": [{"project_id": str(project.id), "action": "legate",
                                     "successor_email": "bob@zelian.fr"}]})
        assert resp.status_code == status.HTTP_200_OK
        op = resp.data["operations"][0]
        assert op["result"] == "legated"  # RM-08
        project.refresh_from_db()
        assert project.project_lead_id == bob.pk
        pm = ProjectMember.objects.get(project=project, member=bob)
        assert pm.role == 20 and pm.is_active is True  # ADMIN actif
        assert Issue.all_objects.filter(pk=issue.pk).exists()  # contenu intact

    def test_legation_to_inactive_member_refused(self, workspace, configured):
        alice = _make_user("alice@zelian.fr")
        bob = _make_user("bob@zelian.fr")
        _member(workspace, alice)
        _member(workspace, bob, is_active=False)  # repreneur inactif (cas 7)
        project = _project(workspace, alice)

        resp = _treat({"email": "alice@zelian.fr", "account": "mask",
                       "projects": [{"project_id": str(project.id), "action": "legate",
                                     "successor_email": "bob@zelian.fr"}]})
        op = resp.data["operations"][0]
        assert op["result"] == "rejected"
        assert op["reason"] == "successor_not_active_member"
        project.refresh_from_db()
        assert project.project_lead_id is None  # inchangé

    def test_legation_is_idempotent(self, workspace, configured):
        alice = _make_user("alice@zelian.fr")
        bob = _make_user("bob@zelian.fr")
        _member(workspace, alice)
        _member(workspace, bob)
        project = _project(workspace, alice)
        payload = {"email": "alice@zelian.fr", "account": "mask",
                   "projects": [{"project_id": str(project.id), "action": "legate",
                                 "successor_email": "bob@zelian.fr"}]}
        _treat(payload)
        resp = _treat(payload)
        assert resp.data["operations"][0]["result"] == "legated"
        assert ProjectMember.objects.filter(project=project, member=bob).count() == 1


@pytest.mark.contract
@pytest.mark.django_db
class TestZelianTracesGuards:
    def test_unknown_person_is_noop(self, workspace, configured):
        resp = _treat({"email": "ghost@zelian.fr", "account": "delete"})
        assert resp.data["status"] == "unknown"  # cas 6

    def test_bot_is_protected(self, workspace, create_bot_user, configured):
        resp = _treat({"email": create_bot_user.email, "account": "delete"})
        assert resp.data["status"] == "protected"  # RM-09
        assert User.objects.filter(pk=create_bot_user.pk).exists()

    def test_missing_secret_is_forbidden(self, workspace, configured):
        resp = _treat({"email": "x@zelian.fr", "account": "mask"}, key=None)
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_fail_closed_when_secret_not_configured(self, workspace, monkeypatch):
        monkeypatch.delenv("ZELIAN_PROVISIONING_SECRET", raising=False)
        resp = _treat({"email": "x@zelian.fr", "account": "mask"}, key="anything")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_treatment_is_silent(self, workspace, configured):
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        _treat({"email": "alice@zelian.fr", "account": "delete"})
        assert len(mail.outbox) == 0  # RM-11


@pytest.mark.contract
@pytest.mark.django_db
class TestZelianTracesS3:
    def test_attachment_purge_schedules_s3_deletion(
        self, workspace, configured, monkeypatch, django_capture_on_commit_callbacks
    ):
        calls = {}

        class FakeStorage:
            def delete_files(self, object_names):
                calls["keys"] = list(object_names)

        monkeypatch.setattr(
            "plane.app.views.zelian.traces.S3Storage", FakeStorage
        )
        alice = _make_user("alice@zelian.fr")
        _member(workspace, alice)
        project = _project(workspace, alice)
        issue = _issue(workspace, project, alice)
        asset = _asset(workspace, alice, project=project, issue=issue)

        with django_capture_on_commit_callbacks(execute=True):
            resp = _treat({"email": "alice@zelian.fr", "account": "mask",
                           "categories": {"attachments": True}})
        assert resp.status_code == status.HTTP_200_OK
        assert FileAsset.all_objects.filter(pk=asset.pk).exists() is False
        assert asset.asset.name in calls.get("keys", [])

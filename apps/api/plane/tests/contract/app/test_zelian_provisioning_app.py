# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""
Contract tests — module api/provisioning-zelian (US-01 -> US-05).

L'endpoint server-to-server ``POST /api/zelian/provisioning/`` provisionne /
désactive des membres du workspace depuis l'annuaire Zelian, sécurisé par un
secret de service (en-tête ``X-Zelian-Provisioning-Key``). Ces tests couvrent
les critères d'acceptation de spec-fonctionnel.md (coche, adoption, idempotence,
désactivation/réactivation, rôles, cascade Invité, habilitation, comptes protégés,
lot partiel).
"""

import uuid

import pytest
from django.core import mail
from rest_framework import status
from rest_framework.test import APIClient

from plane.db.models import Project, ProjectMember, User, WorkspaceMember

URL = "/api/zelian/provisioning/"
SECRET = "test-provisioning-secret"


@pytest.fixture
def configured(monkeypatch):
    """Secret de provisioning configuré (via le fallback env de get_configuration_value)."""
    monkeypatch.setenv("ZELIAN_PROVISIONING_SECRET", SECRET)
    return SECRET


def _post(members, *, slug="test-workspace", key=SECRET):
    client = APIClient()
    extra = {"HTTP_X_ZELIAN_PROVISIONING_KEY": key} if key is not None else {}
    return client.post(
        URL,
        {"workspace_slug": slug, "members": members},
        format="json",
        **extra,
    )


def _make_user(email, **kwargs):
    local = email.split("@")[0]
    user = User.objects.create(email=email, username=uuid.uuid4().hex, first_name=local, **kwargs)
    user.set_password("x")
    user.save()
    return user


@pytest.mark.contract
@pytest.mark.django_db
class TestZelianProvisioning:
    # ----- US-01 : coche -----
    def test_provision_unknown_email_creates_active_member(self, workspace, configured):
        resp = _post([{"email": "newcomer@zelian.fr"}])
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["results"][0]["status"] == "created"
        user = User.objects.get(email__iexact="newcomer@zelian.fr")
        assert user.is_password_autoset is True  # entrée par SSO, pas de mot de passe
        member = WorkspaceMember.objects.get(workspace=workspace, member=user)
        assert member.is_active is True
        assert member.role == 15  # RM-03 défaut Membre
        assert len(mail.outbox) == 0  # RM-11 silencieux

    def test_provision_adopts_existing_user_no_duplicate(self, workspace, configured):
        existing = _make_user("already@zelian.fr")
        resp = _post([{"email": "already@zelian.fr"}])
        assert resp.data["results"][0]["status"] == "adopted"
        assert User.objects.filter(email__iexact="already@zelian.fr").count() == 1  # RM-02
        assert WorkspaceMember.objects.filter(workspace=workspace, member=existing).exists()

    def test_provision_is_idempotent(self, workspace, configured):
        _post([{"email": "idem@zelian.fr"}])
        resp = _post([{"email": "idem@zelian.fr"}])
        assert resp.data["results"][0]["status"] == "unchanged"  # RM-10
        assert WorkspaceMember.objects.filter(member__email__iexact="idem@zelian.fr").count() == 1

    # ----- US-03 / US-04 : décoche / recoche -----
    def test_deactivate_keeps_data_and_is_reversible(self, workspace, configured):
        _post([{"email": "leaver@zelian.fr"}])
        user = User.objects.get(email__iexact="leaver@zelian.fr")

        resp = _post([{"email": "leaver@zelian.fr", "action": "deactivate"}])
        assert resp.data["results"][0]["status"] == "deactivated"
        member = WorkspaceMember.objects.get(workspace=workspace, member=user)
        assert member.is_active is False  # RM-05 : désactivé, pas supprimé
        assert User.objects.filter(pk=user.pk).exists()

        # recoche -> réactivation à l'identique
        resp = _post([{"email": "leaver@zelian.fr"}])
        assert resp.data["results"][0]["status"] == "reactivated"
        member.refresh_from_db()
        assert member.is_active is True

    # ----- US-05 : rôles -----
    def test_role_omitted_leaves_existing_role_intact(self, workspace, configured):
        user = _make_user("boss@zelian.fr")
        WorkspaceMember.objects.create(workspace=workspace, member=user, role=20, is_active=True)
        resp = _post([{"email": "boss@zelian.fr"}])  # pas de role
        assert resp.data["results"][0]["status"] == "unchanged"
        assert WorkspaceMember.objects.get(workspace=workspace, member=user).role == 20  # RM-04

    def test_role_provided_is_applied(self, workspace, configured):
        user = _make_user("promoted@zelian.fr")
        WorkspaceMember.objects.create(workspace=workspace, member=user, role=15, is_active=True)
        resp = _post([{"email": "promoted@zelian.fr", "role": 20}])
        assert resp.data["results"][0]["status"] == "role_updated"
        assert WorkspaceMember.objects.get(workspace=workspace, member=user).role == 20

    def test_guest_role_cascades_to_projects(self, workspace, create_user, configured):
        project = Project.objects.create(
            name="P", identifier="P", workspace=workspace, created_by=create_user
        )
        user = _make_user("demote@zelian.fr")
        WorkspaceMember.objects.create(workspace=workspace, member=user, role=15, is_active=True)
        pm = ProjectMember.objects.create(
            workspace=workspace, project=project, member=user, role=15, is_active=True
        )
        _post([{"email": "demote@zelian.fr", "role": 5}])
        pm.refresh_from_db()
        assert pm.role == 5  # RM-14 cascade Invité

    # ----- habilitation (RM-13) -----
    def test_missing_secret_header_is_forbidden(self, workspace, configured):
        resp = _post([{"email": "x@zelian.fr"}], key=None)
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert not User.objects.filter(email__iexact="x@zelian.fr").exists()

    def test_wrong_secret_is_forbidden(self, workspace, configured):
        resp = _post([{"email": "x@zelian.fr"}], key="wrong")
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert not User.objects.filter(email__iexact="x@zelian.fr").exists()

    def test_fail_closed_when_secret_not_configured(self, workspace, monkeypatch):
        monkeypatch.delenv("ZELIAN_PROVISIONING_SECRET", raising=False)
        resp = _post([{"email": "x@zelian.fr"}], key="anything")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    # ----- comptes protégés (RM-09) -----
    def test_bot_account_is_protected(self, workspace, create_bot_user, configured):
        resp = _post([{"email": create_bot_user.email}])
        assert resp.data["results"][0]["status"] == "protected"
        assert not WorkspaceMember.objects.filter(
            workspace=workspace, member=create_bot_user
        ).exists()

    def test_superuser_account_is_protected(self, workspace, configured):
        admin = _make_user("root@zelian.fr", is_superuser=True)
        resp = _post([{"email": "root@zelian.fr"}])
        assert resp.data["results"][0]["status"] == "protected"
        assert not WorkspaceMember.objects.filter(workspace=workspace, member=admin).exists()

    # ----- lot partiel (cas limite 9) -----
    def test_invalid_element_rejected_others_processed(self, workspace, configured):
        resp = _post(
            [
                {"email": "not-an-email"},
                {"email": "valid@zelian.fr", "role": 99},  # rôle invalide
                {"email": "good@zelian.fr"},
            ]
        )
        by_email = {r.get("email"): r for r in resp.data["results"]}
        assert by_email["not-an-email"]["status"] == "rejected"
        assert by_email["valid@zelian.fr"]["status"] == "rejected"
        assert by_email["good@zelian.fr"]["status"] == "created"
        assert User.objects.filter(email__iexact="good@zelian.fr").exists()

    # ----- garde-fous de requête -----
    def test_missing_workspace_slug_is_bad_request(self, configured):
        client = APIClient()
        resp = client.post(
            URL, {"members": [{"email": "x@zelian.fr"}]},
            format="json", HTTP_X_ZELIAN_PROVISIONING_KEY=SECRET,
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_unknown_workspace_is_not_found(self, configured):
        resp = _post([{"email": "x@zelian.fr"}], slug="does-not-exist")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

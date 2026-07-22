# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Provisioning server-to-server des membres du workspace depuis l'annuaire Zelian.
# Contrat : docs/specs/api/provisioning-zelian/spec-fonctionnel.md.
# Auth : secret de service partagé (convention Insider §9.11 / §8.7, règle 07) —
# jamais le JWT identité ni un token utilisateur.

import logging
import uuid

from django.db import transaction
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseAPIView
from plane.app.views.zelian._base import ZelianServiceAuthMixin
from plane.db.models import ProjectMember, User, Workspace, WorkspaceMember

logger = logging.getLogger("plane.zelian.provisioning")

VALID_ROLES = {20, 15, 5}
GUEST_ROLE = 5
DEFAULT_ROLE = 15  # RM-03 : Membre par défaut à la création
MAX_BATCH = 1000


class ZelianProvisioningEndpoint(ZelianServiceAuthMixin, BaseAPIView):
    """Provisionne / désactive des membres du workspace depuis l'annuaire Zelian.

    Appel **server-to-server** (le service de synchronisation Zelian, pas un humain),
    sécurisé par un **secret de service** partagé en en-tête ``X-Zelian-Provisioning-Key``,
    lu via ``get_configuration_value`` (config instance chiffrable / env — gestionnaire de
    secrets, règle 07 Insider) et comparé en temps constant. **Fail-closed** si le secret
    n'est pas configuré.

    Invariants (spec) : jointure par email (RM-02) ; ne touche jamais un compte de service
    ``is_bot`` ni un super-admin (RM-09) ; rôle transmis = appliqué / omis = inchangé
    (RM-04) ; désactivation réversible sans perte (RM-05) ; idempotent (RM-10) ; silencieux
    (RM-11) ; cascade Invité (RM-14). **Zéro migration** (ADR-002) : réutilise ``User`` et
    ``WorkspaceMember``.
    """

    def post(self, request):
        if not self._is_authorized(request):
            # RM-13 : appelant non habilité -> rejet sans effet, cause non divulguée.
            return Response(
                {"error": "Provisioning not authorized"},
                status=status.HTTP_403_FORBIDDEN,
            )

        slug = request.data.get("workspace_slug")
        if not slug:
            return Response(
                {"error": "workspace_slug is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        workspace = Workspace.objects.filter(slug=slug).first()
        if workspace is None:
            return Response(
                {"error": "Workspace not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        members = request.data.get("members")
        if not isinstance(members, list) or not members:
            return Response(
                {"error": "members must be a non-empty list"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(members) > MAX_BATCH:
            return Response(
                {"error": f"batch too large (max {MAX_BATCH})"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        results = [self._process_one(workspace, item) for item in members]
        summary = {}
        for result in results:
            summary[result["status"]] = summary.get(result["status"], 0) + 1
        logger.info(
            "zelian-provisioning batch workspace=%s size=%s summary=%s",
            workspace.slug,
            len(members),
            summary,
        )
        return Response(
            {"results": results, "summary": summary},
            status=status.HTTP_200_OK,
        )

    def _process_one(self, workspace, item):
        if not isinstance(item, dict):
            return {"email": None, "status": "rejected", "reason": "invalid_item"}

        email = (item.get("email") or "").strip().lower()
        action = item.get("action") or "provision"
        role = item.get("role")

        if not email or "@" not in email:
            return {"email": email, "status": "rejected", "reason": "invalid_email"}
        if role is not None and role not in VALID_ROLES:
            return {"email": email, "status": "rejected", "reason": "invalid_role"}
        if action not in ("provision", "deactivate"):
            return {"email": email, "status": "rejected", "reason": "invalid_action"}

        try:
            with transaction.atomic():
                user = User.objects.filter(email__iexact=email).first()

                # RM-09 : jamais un compte de service ni un super-admin.
                if user is not None and (user.is_bot or user.is_superuser):
                    return {"email": email, "status": "protected"}

                if action == "deactivate":
                    return self._deactivate(workspace, user, email)
                return self._provision(workspace, user, email, role, item)
        except Exception:  # un élément fautif ne casse pas le lot (cas limite 9)
            logger.exception("zelian-provisioning failed for %s", email)
            return {"email": email, "status": "rejected", "reason": "error"}

    def _deactivate(self, workspace, user, email):
        if user is None:
            return {"email": email, "status": "unknown"}
        member = WorkspaceMember.objects.filter(
            workspace=workspace, member=user
        ).first()
        if member is None or not member.is_active:
            return {"email": email, "status": "unchanged"}
        # RM-05 : désactivation réversible, aucune donnée supprimée.
        member.is_active = False
        member.save(update_fields=["is_active"])
        logger.info(
            "zelian-provisioning deactivate email=%s workspace=%s",
            email,
            workspace.slug,
        )
        return {"email": email, "status": "deactivated"}

    def _provision(self, workspace, user, email, role, item):
        created_user = False
        if user is None:
            # Création d'un compte sans mot de passe : l'entrée se fera par le SSO Zelian.
            first_name, _, last_name = (item.get("name") or "").strip().partition(" ")
            user = User(email=email, username=uuid.uuid4().hex)
            user.set_password(uuid.uuid4().hex)
            user.is_password_autoset = True
            user.is_email_verified = True
            user.first_name = first_name
            user.last_name = last_name
            user.save()
            created_user = True
        elif user.masked_at is not None:
            # US-06 companion : recoche d'un compte « Profil supprimé » (traces conservées,
            # RM-07) -> on lève le tombstone et on restaure une identité propre depuis
            # l'annuaire, sinon le revenant resterait marqué et bloqué au login (gate GHSA).
            name = (item.get("name") or "").strip()
            user.first_name, _, user.last_name = name.partition(" ")
            user.display_name = name  # vide -> recalculé (préfixe email) par User.save()
            user.avatar = ""
            user.masked_at = None
            user.last_logout_time = None
            user.is_active = True
            user.save()
        elif not user.is_active:
            user.is_active = True
            user.save(update_fields=["is_active"])

        member = WorkspaceMember.objects.filter(
            workspace=workspace, member=user
        ).first()
        if member is None:
            # RM-02 : adoption d'un compte existant, jamais de doublon.
            member = WorkspaceMember.objects.create(
                workspace=workspace,
                member=user,
                role=role if role is not None else DEFAULT_ROLE,
            )
            outcome = "created" if created_user else "adopted"
        else:
            changed = []
            if not member.is_active:
                member.is_active = True
                changed.append("is_active")
            # RM-04 : rôle transmis => appliqué ; rôle omis => inchangé.
            if role is not None and member.role != role:
                member.role = role
                changed.append("role")
            if changed:
                member.save(update_fields=changed)
                outcome = "reactivated" if "is_active" in changed else "role_updated"
            else:
                outcome = "unchanged"  # RM-10 : idempotence

        # RM-14 : le passage à Invité rétrograde tous ses projets (cascade RETRO-011).
        if role == GUEST_ROLE:
            ProjectMember.objects.filter(
                workspace=workspace, member=user
            ).update(role=GUEST_ROLE)

        logger.info(
            "zelian-provisioning %s email=%s workspace=%s role=%s",
            outcome,
            email,
            workspace.slug,
            member.role,
        )
        return {"email": email, "status": outcome, "role": member.role}

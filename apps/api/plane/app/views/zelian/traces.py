# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Traitement des traces d'une personne supprimée de l'annuaire Zelian (US-06).
# Contrat : docs/specs/api/provisioning-zelian/spec-fonctionnel.md.
# Services rendus par Plane (le dialogue UI vit dans Manage, hors scope) :
#   - inventaire des contributions (lecture) ;
#   - purge sélective / totale DÉFINITIVE (RM-06 : ne réapparaît jamais) ;
#   - légation de projet à un repreneur membre actif (RM-08) ;
#   - marquage « Profil supprimé — [Nom] » si conservation des traces (RM-07).
# Idempotent (RM-10), silencieux (RM-11), ne touche jamais un compte hors annuaire (RM-09),
# appelant non habilité rejeté (RM-13). Zéro migration (ADR-002).
#
# INVARIANTS DE SÛRETÉ (revue adversariale) :
#  - `user.delete()` est une cascade GLOBALE : avant, désamorcer la landmine `IssueActivity`
#    (DO_NOTHING) et neutraliser TOUT conteneur partagé qui pointe la personne en CASCADE.
#  - Les self-FK `parent` (Issue/IssueComment/Page) sont CASCADE : hard-deleter un parent
#    emporte les enfants d'AUTRUI (destruction + IssueActivity orphelin). On **détache** donc
#    les enfants d'autrui (préservés) et on **étend** la suppression aux descendants PROPRES.
#  - Purge = hard delete (`all_objects...delete()` / `instance.delete(soft=False)`).

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.views.base import BaseAPIView
from plane.app.views.zelian._base import ZelianServiceAuthMixin
from plane.db.models import (
    Cycle,
    FileAsset,
    GithubRepositorySync,
    Issue,
    IssueActivity,
    IssueComment,
    IssueDescriptionVersion,
    IssueVersion,
    IssueView,
    Module,
    Page,
    PageVersion,
    Project,
    ProjectMember,
    User,
    Workspace,
    WorkspaceIntegration,
    WorkspaceMember,
    WorkspaceTheme,
)
from plane.settings.storage import S3Storage

logger = logging.getLogger("plane.zelian.traces")

ADMIN_ROLE = 20
ALL_CATEGORIES = {"issues": True, "comments": True, "pages": True, "attachments": True}


# --------------------------------------------------------------------------- #
# Helpers requête                                                             #
# --------------------------------------------------------------------------- #
def _forbidden():
    # RM-13 : appelant non habilité -> rejet sans effet, cause non divulguée.
    return Response(
        {"error": "Not authorized"}, status=status.HTTP_403_FORBIDDEN
    )


def _resolve_workspace(request):
    slug = request.data.get("workspace_slug")
    if not slug:
        return None, Response(
            {"error": "workspace_slug is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    workspace = Workspace.objects.filter(slug=slug).first()
    if workspace is None:
        return None, Response(
            {"error": "Workspace not found"}, status=status.HTTP_404_NOT_FOUND
        )
    return workspace, None


def _clean_email(request):
    return (request.data.get("email") or "").strip().lower()


# --------------------------------------------------------------------------- #
# Helpers purge (hard delete DÉFINITIF — RM-06)                                #
# --------------------------------------------------------------------------- #
def _collect_s3_keys(asset_qs, s3_keys):
    """Mémorise les clés d'objets S3 des FileAsset AVANT leur suppression en base
    (elles servent à l'effacement physique post-commit — RGPD droit à l'effacement)."""
    for name in asset_qs.values_list("asset", flat=True):
        if name:
            s3_keys.append(name)


def _delete_s3(keys):
    """Effacement physique des objets S3/MinIO — best effort, découplé de la purge BDD."""
    try:
        S3Storage().delete_files(object_names=keys)
    except Exception:
        logger.exception("zelian-traces S3 deletion failed (%s keys)", len(keys))


def _own_closure_detach_foreign(model, ids, author_field, user):
    """Étend `ids` à la fermeture (self-FK ``parent``) des descendants DE LA PERSONNE, et
    **détache** (``parent=None``) les descendants d'AUTRUI pour les préserver.

    ``parent`` est ``on_delete=CASCADE`` sur Issue/IssueComment/Page : sans ça, hard-deleter
    un parent détruirait les sous-issues/réponses/sous-pages d'autrui et orphelinerait leurs
    ``IssueActivity`` (DO_NOTHING) -> IntegrityError. Renvoie la liste complète à supprimer.
    """
    full = list(ids)
    frontier = list(ids)
    while frontier:
        # descendants d'autrui -> détachés (préservés, non cascadés)
        model.all_objects.filter(parent_id__in=frontier).exclude(
            **{author_field: user}
        ).update(parent=None)
        # descendants propres -> ajoutés à la fermeture (purgés avec)
        seen = set(full)
        children = list(
            model.all_objects.filter(parent_id__in=frontier, **{author_field: user})
            .exclude(id__in=seen)
            .values_list("id", flat=True)
        )
        full.extend(children)
        frontier = children
    return full


def _purge_issue_ids(ids, user, s3_keys=None):
    """Hard-delete des issues de la personne (+ leurs sous-issues PROPRES), en détachant les
    sous-issues d'autrui et en désamorçant la landmine ``IssueActivity`` (DO_NOTHING) pour
    l'ensemble réellement supprimé (issues + tous leurs commentaires)."""
    if not ids:
        return
    all_ids = _own_closure_detach_foreign(Issue, ids, "created_by", user)
    # Clés S3 des pièces jointes sur TOUTES les issues supprimées (fermeture incluse) —
    # collectées ici pour couvrir les sous-issues propres ajoutées par la fermeture.
    if s3_keys is not None:
        _collect_s3_keys(
            FileAsset.all_objects.filter(issue_id__in=all_ids), s3_keys
        )
    # Commentaires sur ces issues (de quiconque) : cascadés à la suppression de l'issue.
    comment_ids = list(
        IssueComment.all_objects.filter(issue_id__in=all_ids).values_list(
            "id", flat=True
        )
    )
    if s3_keys is not None and comment_ids:
        _collect_s3_keys(
            FileAsset.all_objects.filter(comment_id__in=comment_ids), s3_keys
        )
    IssueActivity.all_objects.filter(issue_id__in=all_ids).delete()
    if comment_ids:
        IssueActivity.all_objects.filter(
            issue_comment_id__in=comment_ids
        ).delete()
    Issue.all_objects.filter(id__in=all_ids).delete()


def _purge_comment_ids(ids, user):
    """Hard-delete des commentaires de la personne (+ ses réponses PROPRES), en détachant les
    réponses d'autrui et en désamorçant la landmine ``IssueActivity``."""
    if not ids:
        return
    all_ids = _own_closure_detach_foreign(IssueComment, ids, "actor", user)
    IssueActivity.all_objects.filter(issue_comment_id__in=all_ids).delete()
    IssueComment.all_objects.filter(id__in=all_ids).delete()


def _page_is_shared(page, user):
    """Une page est partagée si un AUTRE a une version dessus OU une sous-page dessus."""
    if PageVersion.all_objects.filter(page=page).exclude(owned_by=user).exists():
        return True
    return Page.all_objects.filter(parent=page).exclude(owned_by=user).exists()


def _purge_pages(page_qs, user, s3_keys, owner_id):
    """Purge les pages de la personne, mais **préserve** (réattribue à l'owner) celles qu'un
    autre a éditées ou sous-pagées (cas 8 appliqué aux pages). Avant chaque suppression, on
    détache les sous-pages (celles d'autrui sont ainsi préservées ; les siennes sont traitées
    indépendamment par la boucle). Renvoie le nombre de pages effectivement purgées."""
    purged = 0
    for page in list(page_qs):
        if _page_is_shared(page, user):
            Page.all_objects.filter(pk=page.pk).update(owned_by_id=owner_id)
            PageVersion.all_objects.filter(page=page, owned_by=user).update(
                owned_by_id=owner_id
            )
        else:
            # Détache toute sous-page avant le hard-delete (évite la cascade Page.parent).
            Page.all_objects.filter(parent_id=page.pk).update(parent=None)
            _collect_s3_keys(
                FileAsset.all_objects.filter(page_id=page.pk), s3_keys
            )
            page.delete(soft=False)
            purged += 1
    return purged


def _purge_person_workspace(workspace, user, cats, legated_ids, s3_keys, owner_id):
    """Purge les contributions de la personne dans le workspace, par catégorie.

    Exclut les projets légués (RM-08 : leur contenu reste intact). Les pages partagées sont
    préservées (réattribuées). Renvoie les compteurs.
    """
    counts = {}
    if cats.get("issues"):
        qs = Issue.all_objects.filter(workspace=workspace, created_by=user)
        if legated_ids:
            qs = qs.exclude(project_id__in=legated_ids)
        ids = list(qs.values_list("id", flat=True))
        _purge_issue_ids(ids, user, s3_keys)
        counts["issues"] = len(ids)
    if cats.get("comments"):
        qs = IssueComment.all_objects.filter(workspace=workspace, actor=user)
        if legated_ids:
            qs = qs.exclude(project_id__in=legated_ids)
        cids = list(qs.values_list("id", flat=True))
        _collect_s3_keys(FileAsset.all_objects.filter(comment_id__in=cids), s3_keys)
        _purge_comment_ids(cids, user)
        counts["comments"] = len(cids)
    if cats.get("pages"):
        page_qs = Page.all_objects.filter(workspace=workspace, owned_by=user)
        counts["pages"] = _purge_pages(page_qs, user, s3_keys, owner_id)
    if cats.get("attachments"):
        aqs = FileAsset.all_objects.filter(
            workspace=workspace,
            user=user,
            entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
        )
        if legated_ids:
            aqs = aqs.exclude(project_id__in=legated_ids)
        aids = list(aqs.values_list("id", flat=True))
        _collect_s3_keys(FileAsset.all_objects.filter(id__in=aids), s3_keys)
        FileAsset.all_objects.filter(id__in=aids).delete()
        counts["attachments"] = len(aids)
    return counts


def _purge_person_in_project(project, user, s3_keys, owner_id):
    """Purge uniquement les items de la personne DANS ce projet (cas 8 : projet partagé —
    on ne détruit pas le contenu d'autrui)."""
    counts = {}
    ids = list(
        Issue.all_objects.filter(project=project, created_by=user).values_list(
            "id", flat=True
        )
    )
    _purge_issue_ids(ids, user, s3_keys)
    counts["issues"] = len(ids)

    cids = list(
        IssueComment.all_objects.filter(
            project=project, actor=user
        ).values_list("id", flat=True)
    )
    _collect_s3_keys(FileAsset.all_objects.filter(comment_id__in=cids), s3_keys)
    _purge_comment_ids(cids, user)
    counts["comments"] = len(cids)

    page_qs = Page.all_objects.filter(projects=project, owned_by=user)
    counts["pages"] = _purge_pages(page_qs, user, s3_keys, owner_id)

    aqs = FileAsset.all_objects.filter(project=project, user=user)
    _collect_s3_keys(aqs, s3_keys)
    counts["attachments"] = aqs.count()
    aqs.delete()
    return counts


def _is_shared(project, user):
    """Un projet est partagé dès qu'il porte des membres OU des contributions d'autrui.

    Conservateur (on préfère préserver que détruire) : couvre membres (actifs ou non), issues,
    commentaires, pages, cycles, vues, modules, pièces jointes non détenus par la personne.
    """
    others = [
        ProjectMember.all_objects.filter(project=project).exclude(member=user),
        Issue.all_objects.filter(project=project).exclude(created_by=user),
        IssueComment.all_objects.filter(project=project).exclude(actor=user),
        Page.all_objects.filter(projects=project).exclude(owned_by=user),
        Cycle.all_objects.filter(project=project).exclude(owned_by=user),
        IssueView.all_objects.filter(project=project).exclude(owned_by=user),
        Module.all_objects.filter(project=project).exclude(created_by=user),
        FileAsset.all_objects.filter(project=project).exclude(user=user),
    ]
    return any(qs.exists() for qs in others)


# --------------------------------------------------------------------------- #
# Endpoint : inventaire (lecture seule)                                        #
# --------------------------------------------------------------------------- #
class ZelianTracesInventoryEndpoint(ZelianServiceAuthMixin, BaseAPIView):
    """Inventaire des contributions visibles d'une personne (projets créés, work items,
    commentaires, pages, pièces jointes). Alimente le dialogue de suppression de Manage."""

    def post(self, request):
        if not self._is_authorized(request):
            return _forbidden()
        workspace, error = _resolve_workspace(request)
        if error:
            return error
        email = _clean_email(request)
        if not email or "@" not in email:
            return Response(
                {"error": "email is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(email__iexact=email).first()
        if user is None:  # cas 6 : jamais provisionnée -> pas d'erreur, inventaire vide
            return Response(
                {"email": email, "status": "unknown"},
                status=status.HTTP_200_OK,
            )
        if user.is_bot or user.is_superuser:  # RM-09
            return Response(
                {"email": email, "status": "protected"},
                status=status.HTTP_200_OK,
            )

        return Response(
            self._inventory(workspace, user, email), status=status.HTTP_200_OK
        )

    def _inventory(self, workspace, user, email):
        projects = Project.objects.filter(workspace=workspace, created_by=user)
        project_items = []
        for project in projects:
            shared = _is_shared(project, user)
            other_members = (
                ProjectMember.objects.filter(project=project, is_active=True)
                .exclude(member=user)
                .count()
            )
            project_items.append(
                {
                    "project_id": str(project.id),
                    "name": project.name,
                    "identifier": project.identifier,
                    "created_by_departing": True,
                    "counts": {
                        "issues": Issue.objects.filter(
                            project=project, created_by=user
                        ).count(),
                        "comments": IssueComment.objects.filter(
                            project=project, actor=user
                        ).count(),
                        "pages": Page.objects.filter(
                            projects=project, owned_by=user
                        ).count(),
                        "attachments": FileAsset.objects.filter(
                            project=project, user=user
                        ).count(),
                    },
                    "shared": shared,
                    "other_active_members": other_members,
                    "recommend_legation": shared,
                }
            )

        totals = {
            "projects_created": projects.count(),
            "issues_created": Issue.objects.filter(
                workspace=workspace, created_by=user
            ).count(),
            "comments": IssueComment.objects.filter(
                workspace=workspace, actor=user
            ).count(),
            "pages": Page.objects.filter(
                workspace=workspace, owned_by=user
            ).count(),
            "attachments": FileAsset.objects.filter(
                workspace=workspace,
                user=user,
                entity_type=FileAsset.EntityTypeContext.ISSUE_ATTACHMENT,
            ).count(),
        }
        return {
            "email": email,
            "status": "found",
            "user_id": str(user.id),
            "totals": totals,
            "projects": project_items,
        }


# --------------------------------------------------------------------------- #
# Endpoint : traitement (mutation)                                            #
# --------------------------------------------------------------------------- #
class ZelianTracesTreatmentEndpoint(ZelianServiceAuthMixin, BaseAPIView):
    """Applique le traitement décidé dans Manage : légations, purges sélectives/totale,
    marquage « Profil supprimé » ou suppression définitive du compte."""

    def post(self, request):
        if not self._is_authorized(request):
            return _forbidden()
        workspace, error = _resolve_workspace(request)
        if error:
            return error
        email = _clean_email(request)
        if not email or "@" not in email:
            return Response(
                {"error": "email is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        account = request.data.get("account") or "mask"
        if account not in ("mask", "delete"):
            return Response(
                {"error": "account must be 'mask' or 'delete'"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        keep_name = bool(request.data.get("keep_name", True))
        categories = request.data.get("categories") or {}
        if not isinstance(categories, dict):
            categories = {}
        projects = request.data.get("projects") or []
        if not isinstance(projects, list):
            projects = []

        s3_keys = []
        operations = []
        account_result = {"status": "treated", "account": "unchanged"}
        try:
            with transaction.atomic():
                # Verrou sur la personne : sérialise deux synchronisations concurrentes (cas 10).
                user = (
                    User.objects.select_for_update()
                    .filter(email__iexact=email)
                    .first()
                )
                if user is None:  # cas 6
                    return Response(
                        {
                            "email": email,
                            "status": "unknown",
                            "account": "unchanged",
                            "operations": [],
                            "summary": {},
                        },
                        status=status.HTTP_200_OK,
                    )
                if user.is_bot or user.is_superuser:  # RM-09
                    return Response(
                        {
                            "email": email,
                            "status": "protected",
                            "account": "unchanged",
                            "operations": [],
                            "summary": {},
                        },
                        status=status.HTTP_200_OK,
                    )
                # Garde amont : jamais une suppression de compte pour quelqu'un qui possède un
                # workspace (le supprimer le détruirait en cascade). `all_objects` couvre aussi
                # un workspace soft-deleted (que le collector cascaderait quand même).
                if (
                    account == "delete"
                    and Workspace.all_objects.filter(owner=user).exists()
                ):
                    return Response(
                        {
                            "email": email,
                            "status": "rejected",
                            "account": "unchanged",
                            "reason": "owns_workspace",
                            "operations": [],
                            "summary": {},
                        },
                        status=status.HTTP_200_OK,
                    )

                owner_id = workspace.owner_id

                # 1. Légations d'abord — sauvent les projets partagés avant toute purge.
                for entry in projects:
                    if isinstance(entry, dict) and entry.get("action") == "legate":
                        operations.append(self._legate(workspace, user, entry))
                legated_ids = [
                    op["project_id"]
                    for op in operations
                    if op.get("op") == "legate" and op.get("result") == "legated"
                ]

                # 2. Purges de projets entiers (jamais un projet légué).
                for entry in projects:
                    if not isinstance(entry, dict) or entry.get("action") != "purge":
                        continue
                    if str(entry.get("project_id")) in legated_ids:
                        continue  # déjà légué -> ne pas détruire
                    operations.append(
                        self._purge_project(
                            workspace, user, entry, s3_keys, owner_id
                        )
                    )

                # 3. Traitement du compte.
                if account == "delete":
                    account_result = self._delete_account(
                        workspace, user, legated_ids, s3_keys, owner_id
                    )
                else:
                    counts = _purge_person_workspace(
                        workspace, user, categories, legated_ids, s3_keys, owner_id
                    )
                    if any(counts.values()):
                        operations.append(
                            {
                                "op": "purge_categories",
                                "result": "purged",
                                "deleted": counts,
                            }
                        )
                    account_result = self._tombstone(workspace, user, keep_name)

                # 4. Effacement physique S3/MinIO APRÈS commit (découplé, résilient — RGPD).
                if s3_keys:
                    keys = list(dict.fromkeys(s3_keys))  # dédup, ordre préservé
                    transaction.on_commit(lambda: _delete_s3(keys))
        except Exception:
            logger.exception("zelian-traces treatment failed for %s", email)
            return Response(
                {"email": email, "status": "error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        summary = {}
        for op in operations:
            key = op.get("result", "unknown")
            summary[key] = summary.get(key, 0) + 1
        logger.info(
            "zelian-traces treatment email=%s account=%s ops=%s",
            email,
            account,
            summary,
        )
        return Response(
            {
                "email": email,
                "status": account_result.get("status", "treated"),
                "account": account_result.get("account"),
                "reason": account_result.get("reason"),
                "operations": operations,
                "summary": summary,
            },
            status=status.HTTP_200_OK,
        )

    # ----- légation (RM-08 / cas 7) ----- #
    def _legate(self, workspace, user, entry):
        pid = entry.get("project_id")
        successor_email = (entry.get("successor_email") or "").strip().lower()
        project = (
            Project.all_objects.filter(workspace=workspace, id=pid).first()
            if pid
            else None
        )
        if project is None:
            return {
                "op": "legate",
                "project_id": pid,
                "result": "rejected",
                "reason": "project_not_found",
            }
        successor = User.objects.filter(email__iexact=successor_email).first()
        # cas 7 : le repreneur doit être un membre actif du workspace (jamais hors annuaire).
        if (
            successor is None
            or successor.is_bot
            or successor.is_superuser
            or successor.id == user.id
            or not WorkspaceMember.objects.filter(
                workspace=workspace, member=successor, is_active=True
            ).exists()
        ):
            return {
                "op": "legate",
                "project_id": str(project.id),
                "result": "rejected",
                "reason": "successor_not_active_member",
            }

        # Repreneur = ProjectMember ADMIN actif. bulk_create(ignore_conflicts) évite le save()
        # personnalisé de ProjectMember qui crée un ProjectUserProperty (violerait la contrainte
        # unique si le repreneur en a déjà un).
        member = ProjectMember.objects.filter(
            workspace=workspace, project=project, member=successor
        ).first()
        if member is None:
            ProjectMember.objects.bulk_create(
                [
                    ProjectMember(
                        workspace=workspace,
                        project=project,
                        member=successor,
                        role=ADMIN_ROLE,
                        is_active=True,
                    )
                ],
                ignore_conflicts=True,
            )
        else:
            ProjectMember.objects.filter(pk=member.pk).update(
                role=ADMIN_ROLE, is_active=True
            )

        # Repoints via .update() (jamais .save() : éviterait l'écrasement de updated_by).
        updates = {"project_lead": successor}
        if project.default_assignee_id == user.id:
            updates["default_assignee"] = successor
        Project.all_objects.filter(pk=project.pk).update(**updates)
        # Réattribue les conteneurs possédés (owned_by non-nullables) au repreneur : le projet
        # ne dépend plus de la personne -> sûr pour une suppression de compte ultérieure.
        Cycle.all_objects.filter(project=project, owned_by=user).update(
            owned_by=successor
        )
        IssueView.all_objects.filter(project=project, owned_by=user).update(
            owned_by=successor
        )
        Page.all_objects.filter(projects=project, owned_by=user).update(
            owned_by=successor
        )
        # Pièces jointes de la personne dans le projet légué -> repreneur (projet intact).
        FileAsset.all_objects.filter(project=project, user=user).update(
            user=successor
        )
        return {
            "op": "legate",
            "project_id": str(project.id),
            "result": "legated",
            "successor": successor_email,
        }

    # ----- purge d'un projet ----- #
    def _purge_project(self, workspace, user, entry, s3_keys, owner_id):
        pid = entry.get("project_id")
        project = (
            Project.all_objects.filter(workspace=workspace, id=pid).first()
            if pid
            else None
        )
        if project is None:
            return {
                "op": "purge",
                "project_id": pid,
                "result": "unchanged",
                "reason": "not_found",
            }
        # cas 8 : un projet partagé n'est jamais détruit en entier (emporterait le contenu
        # d'autrui) -> on ne purge que les items de la personne ; la légation est la voie
        # recommandée pour transférer un projet partagé.
        if _is_shared(project, user):
            counts = _purge_person_in_project(project, user, s3_keys, owner_id)
            return {
                "op": "purge",
                "project_id": str(project.id),
                "result": "purged_own_only",
                "reason": "shared",
                "deleted": counts,
            }
        # Projet non partagé : suppression dure de tout le projet (cascade issues, comments,
        # activities via project, cycles, pièces jointes...).
        _collect_s3_keys(
            FileAsset.all_objects.filter(project=project), s3_keys
        )
        project.delete(soft=False)
        return {
            "op": "purge",
            "project_id": str(project.id),
            "result": "purged",
        }

    # ----- marquage « Profil supprimé » (RM-07) ----- #
    def _tombstone(self, workspace, user, keep_name):
        if user.masked_at is not None:  # RM-10 : déjà marqué -> idempotent
            return {"status": "treated", "account": "unchanged"}
        now = timezone.now()
        # Tronqué pour tenir dans varchar(255) même après préfixe « Profil supprimé — ».
        original = (
            user.full_name.strip()
            or user.display_name
            or (user.email or "").split("@")[0]
        )[:200]
        label = f"Profil supprimé — {original}" if keep_name else "Profil supprimé"
        User.objects.filter(pk=user.pk).update(
            first_name="Profil supprimé",
            last_name=(f"— {original}" if keep_name else ""),
            display_name=label,
            avatar="",
            avatar_asset=None,
            masked_at=now,
            is_active=False,
            last_logout_time=now,  # gate GHSA : bloque toute réactivation au login SSO
        )
        WorkspaceMember.objects.filter(member=user).update(is_active=False)
        return {"status": "treated", "account": "masked"}

    # ----- suppression définitive du compte (purge totale) ----- #
    def _delete_account(self, workspace, user, legated_ids, s3_keys, owner_id):
        # (La garde owns_workspace a déjà été appliquée en amont de la transaction.)
        # 1. Purge du contenu propre dans le workspace visé (pages partagées préservées).
        _purge_person_workspace(
            workspace,
            user,
            {"issues": True, "comments": True, "pages": True},
            legated_ids,
            s3_keys,
            owner_id,
        )
        # 2. Pièces jointes : réattribuer celles des projets légués (projet intact), purger le
        #    reste (S3 inclus) — sinon la cascade user.delete() les emporte sans effacer S3.
        if legated_ids:
            FileAsset.all_objects.filter(
                user=user, project_id__in=legated_ids
            ).update(user_id=owner_id)
        assets = FileAsset.all_objects.filter(user=user)
        if legated_ids:
            assets = assets.exclude(project_id__in=legated_ids)
        _collect_s3_keys(assets, s3_keys)
        assets.delete()
        # 3. Purge GLOBALE landmine-safe de ses commentaires (TOUS workspaces) : sans ça,
        #    user.delete() cascade les commentaires hors-workspace -> IssueActivity (DO_NOTHING)
        #    orphelins -> IntegrityError. Efface aussi ses commentaires partout.
        remaining_comment_ids = list(
            IssueComment.all_objects.filter(actor=user).values_list("id", flat=True)
        )
        _collect_s3_keys(
            FileAsset.all_objects.filter(comment_id__in=remaining_comment_ids),
            s3_keys,
        )
        _purge_comment_ids(remaining_comment_ids, user)
        # 4. Neutralise GLOBALEMENT tout conteneur partagé qui pointe la personne en CASCADE,
        #    sinon user.delete() détruirait le contenu/les artefacts d'autrui.
        #    (owner_id = owner du workspace visé ; réattribution cross-workspace = compromis
        #    documenté, non destructif — cf. spec-technique.)
        Project.all_objects.filter(project_lead=user).update(project_lead=None)
        Project.all_objects.filter(default_assignee=user).update(
            default_assignee=None
        )
        Cycle.all_objects.filter(owned_by=user).update(owned_by_id=owner_id)
        IssueView.all_objects.filter(owned_by=user).update(owned_by_id=owner_id)
        IssueVersion.all_objects.filter(owned_by=user).update(owned_by_id=owner_id)
        IssueDescriptionVersion.all_objects.filter(owned_by=user).update(
            owned_by_id=owner_id
        )
        # Pages encore détenues (partagées du workspace visé + celles d'autres workspaces) :
        # réattribuées ; leurs versions suivent. (Perso du workspace visé déjà purgées à l'étape 1.)
        Page.all_objects.filter(owned_by=user).update(owned_by_id=owner_id)
        PageVersion.all_objects.filter(owned_by=user).update(owned_by_id=owner_id)
        # Intégrations & thèmes de workspace (partagés) -> réattribués.
        WorkspaceTheme.all_objects.filter(actor=user).update(actor_id=owner_id)
        WorkspaceIntegration.all_objects.filter(actor=user).update(actor_id=owner_id)
        GithubRepositorySync.all_objects.filter(actor=user).update(actor_id=owner_id)
        # 5. Suppression de la ligne User : la cascade n'emporte plus que ses propres données
        #    (profil, préférences, tokens, sessions, memberships, réactions, favoris...).
        #    Les issues d'autres workspaces survivent (created_by SET_NULL). Recoche -> vierge.
        user.delete()
        return {"status": "treated", "account": "deleted"}

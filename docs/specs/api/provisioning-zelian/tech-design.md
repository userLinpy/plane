# Tech Design — Provisioning annuaire Zelian → Plane [DRAFT]

> Intention technique avant implémentation. Sera affiné après la spec fonctionnelle (Phase 3).

## Constats techniques acquis (2026-07-21)

1. **API publique insuffisante** : `workspaces/<slug>/members/` est `http_method_names=["get"]`
   (lecture seule) ; aucun endpoint public ne crée de `WorkspaceMember`.
2. **Un membre assignable = `WorkspaceMember`**, créé nativement uniquement à l'acceptation
   d'invitation (`invite.py`) — d'où le besoin d'une extension ORM.
3. **Intention** : petit endpoint de provisioning côté fork (`User.get_or_create` +
   `WorkspaceMember.get_or_create`), idempotent, **zéro migration** (ADR-002), appelé par
   l'app Manage (ou l'intermédiaire de sync annuaire). Rôles Plane : 20=Admin, 15=Member, 5=Guest.
4. **Sécurité** : endpoint à protéger fortement (création de comptes) — mécanisme d'auth à
   trancher en Phase 3 (token de service dédié vs admin instance). Ne jamais exposer sans auth.
5. **SSO** : les comptes créés sont `is_password_autoset=True` (pas de mot de passe) ; la
   connexion se fait via `api/sso-zelian` (jointure email). Résout aussi le piège
   `DISABLE_WORKSPACE_CREATION=1` (« invitation requise » pour un nouvel utilisateur SSO).

## Tranché en Phase 3 (spec-writer, 2026-07-21 — voir spec-fonctionnel.md)

- **Périmètre** : seuls les cochés dans Manage sont provisionnés (pas de pré-remplissage de
  tout l'annuaire — la note de faisabilité v2 est amendée sur ce point).
- **Contrat rôle** : rôle transmis = appliqué (création + mise à jour), rôle omis = intact
  (RM-04). Le toggle « Plane peut changer les rôles » vit côté Manage (il choisit ce qu'il
  envoie) ; Manage lit les rôles courants via l'API membres existante (lecture seule).
  Défaut : Member (15). Cascade GUEST (RETRO-011) honorée (RM-14).
- **Décoche/départ** : désactivation réversible du membre — jamais de suppression de données
  (RM-05). Pas de kill de session en v1 (accès refusé au prochain contrôle).
- **Suppression de compte (US-06, v1.1)** : services Plane à fournir — inventaire des
  contributions (projets créés, work items, commentaires, pages, pièces jointes), purge
  sélective/totale (définitive, même après recoche — RM-06), légation de projet (repreneur
  membre actif — RM-08), marquage « Profil supprimé — [Nom] » si conservation (RM-07).
- **Email existant** : adoption du compte (jointure email, pas de doublon — RM-02).
- **Silencieux** (RM-11), **idempotent** (RM-10), ne touche jamais les comptes hors annuaire
  (RM-09). Lot ~centaines de personnes en un appel.

## Reste à trancher en Phase 4 (technique)

- Mécanisme d'habilitation de l'appelant (token de service dédié vs admin instance) —
  ne jamais exposer sans auth (RM-13).
- Forme du lot (payload, compte-rendu par élément — cas limite 9) ; verrouillage contre
  les synchronisations concurrentes (cas limite 10).
- Implémentation du marquage « Profil supprimé » et de l'inventaire (portée exacte des
  « contributions visibles ») — v1.1.

## Alternatives écartées

- Appel de l'API REST publique standard (lecture seule → impossible).
- Invitations automatisées (`WorkspaceMemberInvite`) : ne rend pas la personne assignable
  tant qu'elle ne s'est pas connectée — contraire à l'objectif.

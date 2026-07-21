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

## À trancher en Phase 3 (spec-writer)

- Contrat exact (bulk vs unitaire, payload, sémantique de déprovisioning / départs).
- Auth de l'appelant + surface (app interne vs API v1).
- Mapping rôles annuaire Zelian → rôles Plane.
- Comportement sur email déjà existant (compte natif préexistant, collision).

## Alternatives écartées

- Appel de l'API REST publique standard (lecture seule → impossible).
- Invitations automatisées (`WorkspaceMemberInvite`) : ne rend pas la personne assignable
  tant qu'elle ne s'est pas connectée — contraire à l'objectif.

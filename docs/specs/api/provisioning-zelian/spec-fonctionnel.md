# Spec Fonctionnelle — Provisioning annuaire Zelian → Plane [DRAFT]

> **Module** : `api/provisioning-zelian`
> **Statut** : DRAFT (scaffold `/zelian:new-spec` — à rédiger via `/zelian-framework:spec-writer`, Phase 3)
> **Créé le** : 2026-07-21
> **Dépendances** : `api/sso-zelian`, `api/workspaces`

## Contexte

Objectif (cadrage 2026-07-21, note `NOTE-faisabilite-sync-supabase.md` v2) : pré-remplir les
personnes de l'entreprise dans Plane depuis l'annuaire Zelian (Supabase : `referent_profile`
+ `user_permissions`), pour qu'elles soient **cherchables et assignables dès leur arrivée** —
sans invitation manuelle. Une invitation Plane classique ne suffit pas : l'invité non connecté
n'est qu'une ligne « en attente » (`WorkspaceMemberInvite`), ni cherchable ni assignable.
Constat technique acquis : l'API publique (`workspaces/<slug>/members/`) est **en lecture
seule** → le provisioning passe par une extension ORM minime côté fork, exposée en endpoint
et pilotée depuis l'app **Manage** (coche « accès Plane » par personne).
Sens unique : annuaire Zelian → Plane (Plane = réplique). Clé de jointure : **email**.
Note d'évolution (Lucie, 2026-07-21) : le système d'accès Plane côté Manage sera
probablement amélioré/modifié à l'avenir — garder le contrat côté Plane stable et minimal.

## Personas

_À compléter (Phase 3 — spec-writer)._

## Règles métier

_À compléter (Phase 3 — spec-writer)._

## User Stories

_À compléter (Phase 3 — spec-writer)._

## Cas d'usage

_À compléter (Phase 3 — spec-writer)._

## Cas limites

_À compléter (Phase 3 — spec-writer)._

## Contraintes

_À compléter (Phase 3 — spec-writer)._

## Interfaces

_À compléter (Phase 3 — spec-writer)._

## Dépendances

- `api/sso-zelian` — jointure par email : les comptes provisionnés se connectent via le SSO Zelian.
- `api/workspaces` — cible d'écriture : `User` / `WorkspaceMember` du workspace `zelian` (rôles 20/15/5).

## Hors scope

_À compléter (Phase 3 — spec-writer)._

## ADRs référencés

_À compléter (voir politique ADR — catégorie AUTH/INTÉGRATION à évaluer)._

## Critères d'acceptation

_À compléter (Phase 3 — spec-writer)._

# Spec Technique — Provisioning annuaire Zelian → Plane

> **Module** : `api/provisioning-zelian`
> **Statut** : v0.1.0 — US-01→05 implémentés (v1). US-06 (suppression/purge/légation) = v1.1, non implémenté.
> **Mis à jour** : 2026-07-21 (après implémentation).

## Architecture

Un unique endpoint **server-to-server** encapsule tout le contrat côté Plane. Il est isolé dans
un **namespace `zelian` dédié** (comme le SSO), pour minimiser la divergence avec l'upstream et la
surface d'attaque.

- **Route** : `POST /api/zelian/provisioning/` (instance-level ; le workspace visé est passé dans le
  payload, pas dans l'URL).
- **Habilitation** : appel machine (service de synchronisation Zelian), sécurisé par un **secret de
  service** partagé en en-tête `X-Zelian-Provisioning-Key`, lu via `get_configuration_value`
  (config instance chiffrable / fallback env — gestionnaire de secrets, règle 07 Insider) et comparé
  en **temps constant** (`hmac.compare_digest`). **Fail-closed** : sans secret configuré, tout est
  refusé (403). Convention conforme à la doc Insider (`09-architecture-auth §9.11` server-to-server,
  `HUB-TOOLS-ANALYSE §8.7`) — jamais le JWT identité (ES256, réservé aux humains) ni un token
  utilisateur (Plane est un outil tiers).
- **Traitement par lot** : le corps porte une liste `members`, traitée élément par élément dans des
  transactions indépendantes (un élément fautif ne casse pas le lot) ; réponse = statut par élément
  + résumé agrégé. Opérations **journalisées** (`logging`, §9.11).
- **Zéro migration** (ADR-002) : réutilise `User`, `WorkspaceMember`, `ProjectMember`.

Logique par élément (calquée sur `app/views/workspace/invite.py` et l'adaptateur d'auth) :

| Action | Comportement |
|---|---|
| `provision` (défaut) | `User` créé si absent (`is_password_autoset=True`, entrée par SSO) sinon **adopté** (jointure email, RM-02) ; `WorkspaceMember` créé (défaut **Membre 15**, RM-03) ou réactivé ; rôle **transmis = appliqué / omis = inchangé** (RM-04) ; passage à **Invité (5)** ⇒ cascade `ProjectMember` (RM-14, RETRO-011). |
| `deactivate` | `WorkspaceMember.is_active = False` — réversible, aucune donnée supprimée (RM-05). |

Gardes : jamais un compte `is_bot` ni `is_superuser` (RM-09, statut `protected`) ; idempotent (RM-10,
statut `unchanged`) ; silencieux — aucun email (RM-11) ; appelant non habilité rejeté sans effet (RM-13).

## Fichiers créés

- `apps/api/plane/app/views/zelian/provisioning.py` — `ZelianProvisioningEndpoint` (BaseAPIView, `AllowAny` + garde secret).
- `apps/api/plane/app/views/zelian/__init__.py` — export.
- `apps/api/plane/app/urls/zelian.py` — route `zelian/provisioning/`.
- `apps/api/plane/tests/contract/app/test_zelian_provisioning_app.py` — 15 tests de contrat.

## Fichiers modifiés

- `apps/api/plane/app/urls/__init__.py` — import + include de `zelian_urls`.

## Schéma BDD

**Zéro migration** (ADR-002) — réutilise `User`, `WorkspaceMember` (`unique(workspace, member)` quand
`deleted_at IS NULL`), `ProjectMember`. Aucun nouveau modèle.

## API

`POST /api/zelian/provisioning/`

- **En-tête** : `X-Zelian-Provisioning-Key: <secret>` (obligatoire ; 403 sinon / si non configuré).
- **Corps** :
  ```json
  {
    "workspace_slug": "zelian",
    "members": [
      { "email": "prenom.nom@zelian.fr", "role": 15, "action": "provision", "name": "Prénom Nom" }
    ]
  }
  ```
  - `role` optionnel ∈ {20, 15, 5} (défaut 15 à la création ; omis = inchangé sur un membre existant).
  - `action` optionnel ∈ {`provision` (défaut), `deactivate`}.
  - `name` optionnel (renseigne prénom/nom à la création uniquement).
- **Réponse 200** : `{ "results": [ { "email", "status", "role"? , "reason"? } ], "summary": { <status>: <n> } }`.
  - `status` ∈ `created` | `adopted` | `reactivated` | `role_updated` | `unchanged` | `deactivated` | `protected` | `unknown` | `rejected`.
- **Codes** : `403` (non habilité / non configuré), `400` (`workspace_slug`/`members` manquants, lot > 1000), `404` (workspace inconnu).

## Config

- `ZELIAN_PROVISIONING_SECRET` — secret de service (gestionnaire de secrets / env). Non défini ⇒ endpoint désactivé (fail-closed).

## Tests / vérification

- **15 tests de contrat** (`test_zelian_provisioning_app.py`) : création, adoption sans doublon,
  idempotence, désactivation réversible + réactivation, rôle omis/transmis, cascade Invité,
  en-tête manquant/faux, fail-closed, comptes bot/super-admin protégés, lot partiel, gardes
  `workspace_slug`/workspace inconnu.
- **Régression SSO** : `test_zelian_oauth_provider.py` (15) verts — SSO intact.
- `py_compile` OK, zéro migration (`makemigrations --check` attendu clean).

## Hors scope de cette version (US-06, v1.1)

Suppression de compte + traitement des traces (inventaire, purge sélective/totale définitive,
légation de projet, marquage « Profil supprimé »). Cf. spec-fonctionnel.md.

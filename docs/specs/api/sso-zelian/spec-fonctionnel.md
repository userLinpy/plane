# Spec Fonctionnelle — SSO Zelian (Supabase OAuth 2.1 / OIDC)

| Champ   | Valeur                                                                                       |
| ------- | -------------------------------------------------------------------------------------------- |
| Module  | api/sso-zelian                                                                               |
| Version | 0.2.0                                                                                        |
| Date    | 2026-07-20                                                                                   |
| Auteur  | session SSO                                                                                  |
| Statut  | IMPLÉMENTÉ et validé E2E — v0.2.0 (auto-login SSO + front-channel logout livrés, 2026-07-20) |
| Source  | `PLAN-SSO-SUPABASE-PLANE.md` (repo racine)                                                   |

> ⚠️ Feature équivalente au SSO OIDC des plans payants de Plane, réimplémentée en CE **clean-room à partir du provider `gitea`** (AGPL — jamais de copie depuis plane-ee).

---

## Contexte et objectif

Un collaborateur déjà connecté à une app interne Zelian (session Supabase) doit arriver sur Plane **déjà connecté**, sans mot de passe Plane ni invitation. Plane est un outil TIERS : la frontière se franchit par **OIDC/OAuth 2.1**, rôle du serveur OAuth 2.1 de Supabase Auth. Plane CE n'a pas de SSO OIDC natif (payant) mais son module d'auth est extensible par providers (modèle `gitea`, host configurable) + des seams front (`extended.tsx`, `auth-ee.ts`).

## Règles métier

1. Identité maître = Supabase Auth (`auth.users`). Le provider Plane mappe `sub`→`provider_id`, `email`, `name`→prénom/nom, `picture`→avatar.
2. **PKCE S256 obligatoire** (serveur OAuth 2.1) + **`client_secret_basic`** au token endpoint.
3. Le bouton « Continue with Zelian » n'apparaît que si `is_zelian_enabled` (piloté par `IS_ZELIAN_ENABLED=1`).
4. Politique de comptes (configuration, pas code) : `ENABLE_SIGNUP=0` une fois le provisioning en place → seuls les comptes déjà provisionnés entrent (inconnu → `SIGNUP_DISABLED`). Les comptes `client` sont bloqués à la page d'autorisation Supabase (hors repo Plane).
5. `next_path` validé (`validate_next_path`) — pas de redirection ouverte.

## User Stories

- En tant que collaborateur connecté à Onboarding, je clique « Continue with Zelian » → aucun écran → j'arrive dans Plane connecté.
- En tant que collaborateur non connecté, je vois une fois la mire de l'app interne puis je reviens connecté.
- En tant qu'admin d'instance, je continue de me connecter en email + mot de passe (SSO = un provider en plus, pas exclusif).
- En tant qu'utilisateur déconnecté d'une app Zelian, ma session Plane est fermée automatiquement — je n'ai pas à me déconnecter séparément.

## Périmètre livré (repo Plane)

- **Backend** : provider `ZelianOAuthProvider`, endpoints app + space (initiate/callback) avec PKCE, codes d'erreur, exposition `is_zelian_enabled` sur `/api/instances/`, 4 routes.
- **Frontend** : bouton SSO (web + space) via les seams d'extension (zéro modif de fichier core), types + label + logo.
- **Auto-redirect SSO sans clic** (frontend, v1.1) : `auth-root.tsx` — quand `is_zelian_enabled`, l'utilisateur est redirigé automatiquement vers `/auth/zelian/` sans clic. Garde-fous : `?sso=0` (accès au formulaire classique pour un admin) et `error_code` présent (anti-boucle infinie). ⚠️ Premier écart au principe « seams front uniquement » de la v1 : `auth-root.tsx` est un fichier core AGPL — risque de conflit aux merges upstream à surveiller.
- **Front-channel logout** (backend, v1.1) : `ZelianLogoutEndpoint` — route GET `/auth/zelian/logout/` qui ferme la session Django et redirige vers `ZELIAN_POST_LOGOUT_REDIRECT_URL` (config serveur uniquement, jamais de paramètre de requête). Idempotent si aucune session. 7 tests unitaires couvrant la résistance aux redirections ouvertes.
- Provisioning des membres (Mission 2, complémentaire).

## Critères d'acceptation

- [x] Code backend + frontend implémenté, clean-room depuis `gitea`.
- [x] Vérifié offline : 4 routes résolues, `/api/instances/` expose `is_zelian_enabled`, 9 tests unitaires (PKCE, URL, Basic auth, mapping userinfo), typecheck web+space 12/12.
- [x] **Validation E2E** (scénarios §8 du plan) — flux SSO complet validé le 2026-07-13 (local, Supabase dev `jgwcadcexcsifxbbvpzv`) et le 2026-07-20 (mire `@zelian/auth` — auto-login + front-channel logout).
- [x] Front-channel logout : 7 tests unitaires offline, résistance aux redirections ouvertes vérifiée (`next`, `redirect_uri`, `post_logout_redirect_uri`, `//host` ignorés).

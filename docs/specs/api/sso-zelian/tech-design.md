# Tech Design — SSO Zelian (Supabase OAuth 2.1)

> Intention technique. Source : `PLAN-SSO-SUPABASE-PLANE.md`.

## Décisions

1. **Clean-room depuis `gitea`** (provider OAuth CE host-configurable) — jamais de copie plane-ee (AGPL). Le provider gitea est le modèle exact ; on ajoute PKCE S256 + `client_secret_basic`.
2. **Seams front uniquement** (`extended.tsx`, `auth-ee.ts`, `EXTENDED_LOGIN_MEDIUM_LABELS`) → zéro modif de fichier core en v1 (revue + AGPL plus simples). `useOAuthConfig` fusionne déjà core + extended. **Exception v1.1** : l'auto-redirect SSO (décision 5 ci-dessous) a nécessité de toucher `auth-root.tsx` (fichier core AGPL) — seul moyen d'intercepter le rendu avant qu'il apparaisse. L'écart est documenté ; surveiller les merges upstream sur ce fichier.
3. **Zéro migration** : config via `get_configuration_value` (fallback env), pattern identique aux autres providers.
4. **Space** : utiliser les endpoints space dédiés (`/auth/spaces/zelian/`) plutôt que reproduire l'incohérence du core gitea space (qui pointe sur `/auth/gitea/`).
5. **Livré en v1.1** : l'auto-redirect « SSO sans clic » (§6 du plan) — `auth-root.tsx` (core, voir exception décision 2). Garde-fous implémentés : `?sso=0` (accès admin si SSO tombe) et `!error_code` (anti-boucle infinie si le SSO échoue). Retour `<></>` anticipé pendant la redirection pour éviter que le formulaire n'apparaisse une fraction de seconde.

6. **Front-channel logout** : `/auth/sign-out/` n'accepte que POST — une redirection depuis la mire ne le déclenche pas. Un endpoint GET dédié (`ZelianLogoutEndpoint`, route `zelian/logout/`) joue le rôle du `frontchannel_logout_uri` OIDC. Deux contraintes de conception inviolables :
   - La destination de redirection vient **uniquement** de `ZELIAN_POST_LOGOUT_REDIRECT_URL` (config serveur) — jamais d'un paramètre de requête (protection contre l'open redirect).
   - L'endpoint est idempotent : appelable sans savoir si une session Plane existe.
     Risque assumé : « logout CSRF » (une balise image peut forcer la déconnexion) — conséquence limitée à une déconnexion subie, sans accès ni perte de données ; compromis standard du front-channel logout OIDC.

## Alternatives écartées (cf. plan §0)

Plane Commercial + OIDC natif (payant), Google OAuth CE (≠ SSO Supabase), reverse-proxy header (non supporté), réutilisation directe du cookie `.zelian.fr` (Plane = backend tiers hors domaine de confiance).

## Dépendances externes (BLOQUANTES pour l'E2E)

- **Supabase** (§2 du plan, humain) : activer OAuth Server, enregistrer le client `Plane` (Confidential, Redirect URIs exacts avec slash final), clés JWT asymétriques (ES256/RS256 pour `openid`), récupérer Client ID/Secret.
- **Page `/oauth/consent`** (§3) dans l'app du Site URL Supabase (repo externe).
- Vérifier le `.well-known/openid-configuration` réel (bêta) avant test.

## Gouvernance (hors code, à faire en parallèle — plan §9)

- **ADR** « Intégration Plane par l'identité — SSO via serveur OAuth 2.1 Supabase » (catégorie AUTH, whitelist ADR-policy) : étend doc 09 §9.9, amende `HUB-TOOLS-ANALYSE`.
- Registre `05-flux-de-donnees.md` §5.1, note AGPL (fork déjà publié), secrets en gestionnaire (règle 07).

## Risques

- Serveur OAuth Supabase en bêta (Cloud) : contrat susceptible d'évoluer ; re-vérifier si migration self-hosted (doc 09 §9.10).
- Clés JWT HS256 legacy partagées : la migration ES256 doit être coordonnée avec les autres apps Zelian.

# HANDOFF — Module `api/provisioning-zelian` (US-01→06) — 2026-07-22

> Document de reprise **exhaustif** pour une nouvelle session Claude Code. Aucune perte
> d'information ne doit se produire entre la session d'origine et la reprise.
> **Obligation : utiliser le framework Zelian et ses skills** (voir §0).

---

## 0. FRAMEWORK ZELIAN — obligatoire, AVANT tout

1. **Plugin** (dans un terminal) : `claude plugin marketplace update zelian-marketplace` →
   `claude plugin install zelian-framework@zelian-marketplace` → `claude plugin update ...`.
   - État observé le 2026-07-22 : **`zelian-framework` v3.1.0 installé & actif** (mis à jour ce jour).
     Le *refresh* du marketplace échoue (clé SSH `github.com` absente de `known_hosts` — faire
     `ssh -T git@github.com` une fois, ou passer le marketplace en HTTPS). Sans impact (déjà à jour).
   - **⚠️ `superpowers` N'EST PAS installé** dans cet environnement → le workflow
     `/superpowers:write-plan` / `execute-plan` n'est pas invocable. **Reproduire via le mode plan
     natif** (EnterPlanMode/ExitPlanMode). À réinstaller côté interactif si tu veux les skills exacts.
2. **Redémarrer Claude Code**, puis lancer le skill **`/zelian:migrate`** (déjà fait ce jour : le
   marker `.zelian/project.json` est en `framework_version 3.0.0`, `schema_version 1` ; le bump 3.0.0→
   3.1.0 est **cosmétique** — aucun hook ne branche sur `framework_version`, seul `schema_version`
   compte pour l'activation des hooks ; ne pas le bumper sans raison). L'index **Compass**
   (`.zelian/compass.json`) est déjà à 100 % et l'entrée `api/provisioning-zelian` référence les 8
   fichiers zelian (dont les 3 d'US-06).
3. **Pipeline** : spec fonctionnelle déjà là → plan (mode plan natif) → valider → exécuter →
   `@update-writer-after-implement` (hook Stop) pour la doc. **ADR** : politique stricte
   `.claude/rules/06-adr-policy.md` — en cas de doute, `spec-technique.md`, jamais d'ADR.

---

## 1. ÉTAT — ce qui est FAIT (100 % de la spec en-scope)

Le module **`api/provisioning-zelian`** (Manage pilote les accès Plane par une coche « accès Plane » ;
Plane = réplique, sens unique annuaire Zelian → Plane, jointure par **email**) est **entièrement
implémenté et mergé dans `preview`** (`userLinpy/plane`) — **⚠️ côté Plane SEULEMENT (voir §1bis)** :

- **US-01→05 (provisioning)** — **PR #106 MERGÉE** (merge commit `230b8f27e7`). Endpoint
  `POST /api/zelian/provisioning/`.
- **US-06 (traiter les traces)** — **PR #107 MERGÉE le 2026-07-22** (merge commit `46aec5e09b`).
  Endpoints `POST /api/zelian/traces/inventory/` et `POST /api/zelian/traces/treatment/`.

**Couverture spec** (spec-fonctionnel.md — critères d'acceptation & cas limites 1→10) : **complète**.
US-01 coche/adoption, US-02 (côté Plane : le membre existe → pas d'écran « invitation requise » ; le
login réel = feature SSO séparée), US-03 décoche (accès refusé au prochain contrôle, pas de kill de
session — cas 3), US-04 recoche à l'identique, US-05 rôles + cascade Invité (RM-14), US-06 inventaire /
purge sélective-totale définitive / légation / tombstone. RM-01→14, cas 6/7/8/9/10 tous couverts.

**Vérification** : **50 tests** (35 `test_zelian_traces_app.py` + 15 `test_zelian_provisioning_app.py`)
verts ; `makemigrations --check` = zéro migration (ADR-002) ; `ruff check` clean ; copyright OK ; **CI
verte** sur #106 et #107. **Double revue adversariale** de US-06 (12 défauts corrigés, dont 4 blockers
« parent-cascade », voir §6).

---

## 1bis. ⚠️ PÉRIMÈTRE — ceci est la MOITIÉ Plane ; l'échange Manage ↔ Plane n'est PAS fonctionnel

**La feature est un échange à DEUX côtés.** #106/#107 ne livrent que la **moitié Plane (réception)** :
des endpoints server-to-server qui *attendent* d'être appelés. **Rien ne les appelle aujourd'hui** → ils
sont **DORMANTS** ; la feature n'est **pas fonctionnelle end-to-end** tant que la **moitié Manage
(émission)** n'existe pas. C'est **conforme à la spec** (qui scope explicitement « l'interface de Manage »
et « le service de synchronisation » **HORS** du module Plane : Plane = réplique, Manage = pilote) — donc
ce n'est **pas un défaut de la PR #106**, mais **un chantier Manage/Insider distinct et REQUIS**, dans le
monorepo `2026-zelian-insider` (**PAS** le repo Plane).

**Ce que Manage doit implémenter (la moitié ÉMETTRICE)** — par US :
- **US-01 coche** : case « accès Plane » + sélecteur de rôle sur la fiche personne → Manage APPELLE
  `POST /api/zelian/provisioning/` `{workspace_slug, members:[{email, role, action:"provision", name}]}`.
- **US-03 décoche** : même endpoint, `action:"deactivate"`. **US-04 recoche** : `action:"provision"`.
- **US-05 rôle** : inclure/omettre `role` selon le toggle Manage « Plane peut changer les rôles » ; lire
  les rôles courants via l'API membres Plane (lecture seule) pour s'aligner.
- **US-06 suppression** : à la suppression du compte annuaire → `POST /api/zelian/traces/inventory/`
  (peuple le dialogue) puis `POST /api/zelian/traces/treatment/` (décision de l'admin : mask/delete,
  purge par catégorie, légation).
- **Transverse** : Manage **détient le secret** `ZELIAN_PROVISIONING_SECRET` (gestionnaire de secrets) et
  le passe en en-tête `X-Zelian-Provisioning-Key` ; gère le déclencheur (coche / départ) et le dialogue.

→ **C'est le plus gros reliquat de la feature, pas une finition.** Détails en §8.

---

## 2. ACCÈS / ENVIRONNEMENT (CRITIQUE — lire avant de coder)

- **Repo Plane = fork dans WSL.** Deux worktrees :
  - `/home/lucie/plane-security-sso` → branche **`feat/provisioning-zelian-v1`** (ICI vit le code
    provisioning/traces + la spec). **C'est le worktree de travail.**
  - `/home/lucie/dev/plane` → branche **`feat/sso-zelian-auto-login-et-logout`** (SSO — **NE PAS
    TOUCHER**). C'est le worktree **principal**, monté par Docker (voir plus bas).
- **Fichiers** : Read/Edit via UNC `\\wsl.localhost\Ubuntu-24.04\home\lucie\plane-security-sso\...`.
- **Git** : `wsl.exe -d Ubuntu-24.04 -- bash -lc 'git -C /home/lucie/plane-security-sso ...'` (sinon
  « dubious ownership »). **Pas de `$VAR` ni de boucles** dans les commandes passées à `wsl.exe`
  (elles arrivent vides). `gh` est authentifié (compte `userLinpy`, scopes repo+workflow) dans WSL.
- **Docker** : **depuis PowerShell uniquement** (pas depuis WSL — intégration désactivée). Conteneurs :
  `plane-api-1`, `plane-plane-db-1` (Postgres `plane`/`plane`), `plane-plane-redis-1`, `-mq-1`,
  `-minio-1`, workers. `node` : **Windows uniquement** (v24), pas dans WSL.
- **⚠️ PIÈGE MAJEUR — tests** : `plane-api-1` monte `/home/lucie/dev/plane/apps/api` (worktree **SSO**),
  **PAS** le worktree provisioning. La **CI Plane ne lance AUCUN test unitaire** (lint/copyright/codeql/
  build seulement) → **les tests contract sont à lancer en LOCAL**. Recette validée :
  1. `wsl.exe ... 'tar czf /tmp/provtest.tgz -C /home/lucie/plane-security-sso/apps/api --exclude=__pycache__ --exclude="*.pyc" .'`
  2. PowerShell : `docker cp \\wsl.localhost\Ubuntu-24.04\tmp\provtest.tgz plane-api-1:/tmp/provtest.tgz`
  3. `docker exec plane-api-1 sh -lc 'rm -rf /provtest && mkdir -p /provtest && tar xzf /tmp/provtest.tgz -C /provtest'`
  4. Deps de test (une fois par vie du conteneur) : `docker exec plane-api-1 pip install -q pytest==9.0.3 pytest-django==4.5.2 pytest-mock factory-boy freezegun ruff==0.9.7`
  5. `docker exec plane-api-1 sh -lc 'cd /provtest && PYTHONPATH=/provtest python -m pytest plane/tests/contract/app/test_zelian_traces_app.py plane/tests/contract/app/test_zelian_provisioning_app.py --reuse-db -p no:cacheprovider -q'`
  - `pytest.ini` → `DJANGO_SETTINGS_MODULE = plane.settings.test` ; DB de test = `test_plane` (créée
    via migrations, `--reuse-db` pour réutiliser).
  - **Nettoyer les compteurs de throttle avant un run** si besoin :
    `docker exec plane-plane-redis-1 sh -lc "redis-cli --scan --pattern '*throttle*' | xargs -r redis-cli DEL"`.
  - Après modif d'un module Python et si on veut recharger l'app live : `docker restart plane-api-1`.

---

## 3. DOCTRINE / CONFORMITÉ — NE PAS RE-FLAGGER

Recadré par **Lucie le 2026-07-22** (j'avais sur-flaggé) :
- **Plane = outil EXTERNE**, intégré par la **donnée**, traité en tiers (comme GitHub) mais utilisé en
  interne. **Stack séparée** (jamais la BDD Plane dans le cluster Supabase Insider). **Repo SÉPARÉ
  décidé** : le fork `userLinpy/plane` rapatrié en privé sous l'org Zelian, hors du monorepo Insider
  (option monorepo en réserve). L'extension de **provisioning est « isolée et publiable »** = modèle
  **SANCTIONNÉ**. **SSO Zelian conservé** (direction Lucie). Mission du fork : réimplémenter en **CE
  clean-room** (jamais copier `plane-ee`, AGPL) les features Enterprise (SCIM/provisioning, SSO).
- **La déviation vis-à-vis de l'« Architecture ① » du doc Insider `HUB-TOOLS-ANALYSE-auth-clients-et-
  plane.md` n'est PAS un défaut** — c'est le modèle voulu. La `spec-technique` dit désormais que l'auth
  **« s'inspire de / adapte »** `09 §9.11` et `HUB-TOOLS §8.7` (PAS « conforme » : §8.7 prescrit
  « service account + token API scoppé » pour *lire* Plane ; ici les opérations sont **hors surface
  API** → **secret de service instance-level**). **Règle 07** (secret hors repo, dans le gestionnaire de
  secrets) : respectée (`ZELIAN_PROVISIONING_SECRET` en config/env).
- **AGPL** : usage 100 % interne → conforme ; garder `LICENSE`/en-têtes AGPL. Ne PAS modifier l'upstream
  « pour du confort » ; **cherry-pick sécu upstream OK**.
- Docs Insider : `/home/lucie/dev/2026-zelian-insider-docs/docs/` (`09-architecture-auth.md`,
  `11-architecture-insider.md`, `07-conventions.md`, `specs/tools/HUB-TOOLS-ANALYSE-auth-clients-et-plane.md`).

---

## 4. DÉCISIONS GRAVÉES (ne pas re-questionner)

- **Coche-only** (pas de pré-remplissage de tout l'annuaire) ; **jointure email**, adoption sans doublon
  (RM-02) ; défaut **Membre 15** (RM-03) ; **rôle transmis = appliqué / omis = intact** (RM-04, le toggle
  « Plane peut changer les rôles » vit côté Manage) ; passage **Invité (5)** ⇒ cascade `ProjectMember`
  (RM-14) ; **décoche = désactivation réversible** sans perte (RM-05, `WorkspaceMember.is_active=False`).
- **US-06 (cette session, tranché par Lucie)** :
  - **Purge = définitive** (RM-06) = **hard delete** (`.delete(soft=False)` / `all_objects...delete()`),
    jamais soft (qui laisserait la ligne visible via `all_objects`).
  - **Purge totale ⇒ suppression de la ligne `User`** (spec §6-7 « aucune trace / membre vierge » ;
    anonymisation-conservation **écartée**).
  - **Pièces jointes ⇒ effacement physique S3/MinIO** (`S3Storage().delete_files`, en
    `transaction.on_commit`, découplé/résilient — RGPD).
  - **Marquage « Profil supprimé — [Nom] »** (RM-07) via écrasement `display_name`/`first_name`/
    `last_name` + `User.masked_at` (champ dormant, zéro migration) + désactivation
    (`is_active=False` + `last_logout_time`, gate GHSA) ; companion « dé-tombstone » à la recoche.
  - **Throttle sur les échecs d'auth uniquement** (`ZelianFailedAuthThrottle`, 30/min par IP ; **bon
    secret jamais bridé**) au lieu d'exempter tout throttle.
- **Silencieux** (RM-11, aucun email), **idempotent** (RM-10), ne touche **jamais** un compte hors
  annuaire (`is_bot`/`is_superuser` → `protected`, RM-09), appelant non habilité rejeté (RM-13,
  fail-closed sans secret).

---

## 5. ARCHITECTURE TECHNIQUE (fichiers & endpoints)

Namespace `zelian` isolé, endpoints **server-to-server** (secret `X-Zelian-Provisioning-Key`), zéro
migration (réutilise `User`, `WorkspaceMember`, `ProjectMember`, `Project`, `Issue`, `IssueComment`,
`IssueActivity`, `Page`, `PageVersion`, `FileAsset`, `Cycle`, `IssueView`, `Module`, `WorkspaceTheme`,
`WorkspaceIntegration`, `GithubRepositorySync`).

- `apps/api/plane/app/views/zelian/_base.py` — `ZelianServiceAuthMixin` (`_is_authorized` via
  `get_configuration_value` + `compare_digest`, fail-closed) + `ZelianFailedAuthThrottle`.
- `apps/api/plane/app/views/zelian/provisioning.py` — `ZelianProvisioningEndpoint` (US-01→05) ; hérite
  du mixin ; companion « dé-tombstone » dans `_provision`.
- `apps/api/plane/app/views/zelian/traces.py` — `ZelianTracesInventoryEndpoint`,
  `ZelianTracesTreatmentEndpoint` + helpers (`_own_closure_detach_foreign`, `_purge_issue_ids`,
  `_purge_comment_ids`, `_purge_pages`, `_is_shared`, `_page_is_shared`, `_legate`, `_purge_project`,
  `_tombstone`, `_delete_account`).
- `apps/api/plane/app/views/zelian/__init__.py` + `apps/api/plane/app/urls/zelian.py` — exports/routes.
- `apps/api/plane/tests/contract/app/test_zelian_{provisioning,traces}_app.py` — 15 + 35 tests.
- Docs module : `docs/specs/api/provisioning-zelian/{spec-fonctionnel, spec-technique, tech-design,
  VERSIONNING}.md` (+ ce HANDOFF).

**Contrat API `traces/treatment`** (à **figer avec Manage**) :
`{ workspace_slug, email, account: "mask"|"delete", keep_name, categories:{issues,comments,pages,attachments},
projects:[{project_id, action:"purge"|"legate"|"keep", successor_email}] }`. Ordre : légations d'abord →
purges projets → traitement compte. Réponse : `{ status, account, operations[], summary }`.

---

## 6. GOTCHAS TECHNIQUES CRITIQUES (à connaître avant de re-toucher traces.py)

1. **Hard delete** : `objects.delete()` par défaut = **soft** (pose `deleted_at`). Utiliser
   `.delete(soft=False)` (instance `SoftDeleteModel`) ou `all_objects.filter(...).delete()` (manager nu).
   `User` n'est **pas** un `SoftDeleteModel` → `user.delete()` est une **cascade GLOBALE dure**.
2. **Landmine `IssueActivity`** : `IssueActivity.issue` et `.issue_comment` sont `on_delete=DO_NOTHING`
   (seuls DO_NOTHING de tout le modèle). Hard-deleter une Issue/Comment à l'unité laisse des activités
   orphelines → **IntegrityError au COMMIT** (FK Postgres `DEFERRABLE INITIALLY DEFERRED`). On supprime
   les activités d'abord.
3. **Self-FK `parent` = CASCADE** (`Issue.parent`, `IssueComment.parent`, `Page.parent`) — **LE piège
   qui a produit 4 blockers**. Hard-deleter un parent emporte les sous-issues/réponses/sous-pages
   **d'autrui** (destruction + activité orpheline). `_own_closure_detach_foreign` **détache**
   (`parent=None`) les descendants d'autrui (préservés) et **étend** la purge aux descendants **propres**.
4. **Suppression de compte (`_delete_account`)** : neutraliser AVANT `user.delete()` **tout** FK
   `CASCADE→User` pointant du contenu partagé : `Workspace.owner` (refus si en possède, via `all_objects`
   — couvre un workspace soft-deleted), `Project.project_lead`/`default_assignee` (→ null), `Cycle`/
   `IssueView`/`Page`/`PageVersion`/`IssueVersion`/`IssueDescriptionVersion.owned_by` +
   `WorkspaceTheme`/`WorkspaceIntegration`/`GithubRepositorySync.actor` (→ owner du workspace). Purger
   ses commentaires **globalement** (tous workspaces) landmine-safe.
5. **Repoints** : toujours `.update()` (jamais `instance.save()` : `BaseModel.save()` écraserait
   `updated_by` via `crum.get_current_user()` = None en server-to-server).
6. **Légation** : `ProjectMember` créé via `bulk_create(ignore_conflicts=True)` (le `save()` custom de
   `ProjectMember` crée un `ProjectUserProperty` qui violerait la contrainte unique).
7. **Gate SSO** (`authentication/adapter/base.py:326`) : login rejeté si `not is_active AND
   last_logout_time is not None` (correctif GHSA). Toute désactivation user qui doit tenir pose **les
   deux**.
8. **Tests** : `created_by` est auto-nullé par `BaseModel.save()` (crum=None) → utiliser
   `with crum.impersonate(user): Model.objects.create(...)` pour poser l'auteur. Effacement S3 testé
   avec `django_capture_on_commit_callbacks` + mock de `S3Storage`.

---

## 7. LIMITATIONS CONNUES / COMPROMIS (documentés, sûrs, non destructifs)

- **Réattribution cross-workspace** : sur suppression de compte, les conteneurs `owned_by`/`actor` de la
  personne dans un AUTRE workspace sont réattribués à l'owner du workspace **visé** (une purge par
  workspace serait plus fine). Non destructif ; borné en pratique (déploiement mono-workspace `zelian`).
- **Sous-issue PROPRE nichée sous un nœud d'AUTRUI** : préservée (détachée) plutôt que purgée (doctrine
  « préserver plutôt que détruire »). Complétude partielle vs RM-06 sur ce cas de nesting rare.
- **`WorkspaceUserLink.owner`** (liens rapides perso) : cascade-supprimés avec le compte (données perso,
  acceptable).
- **Contrat API `traces`** : proposé, **à figer avec Manage**.

---

## 8. CE QUI RESTE (prochaines étapes)

**Repo Insider** = `/home/lucie/dev/2026-zelian-insider` — brancher depuis `feat/auth_mire_sso`,
**jamais `main`**, PR obligatoire, pipeline Zelian (`/zelian:new-spec` → plan → exécuter).

1. **⭐ LA MOITIÉ ÉMETTRICE (le plus gros morceau — voir §1bis)** — dans Manage/Insider :
   - le **service de synchronisation** qui APPELLE `provisioning` + `traces` (détient le secret, le passe
     en en-tête `X-Zelian-Provisioning-Key`), déclenché par la coche / le départ d'une personne ;
   - la case « **accès Plane** » + sélecteur de rôle sur la fiche personne ;
   - le **dialogue de suppression** peuplé par `traces/inventory`, qui envoie la décision à
     `traces/treatment`.
   Sans ça, les endpoints Plane restent **DORMANTS** (feature non fonctionnelle). Figer d'abord le
   **contrat API `traces`** (§5).
2. **Gouvernance Insider** : l'**ADR « intégration Plane »** (skill `zelian-adr`) + **inscrire Plane au
   registre `05-flux §5.1`** — le doc Insider (§9) l'exigeait *avant de coder*. Convention auth
   server-to-server = « secret de service, gestionnaire de secrets, règle 07 » (voir
   [[zelian-insider-repos-et-conventions]]).
3. **Durcissement optionnel** (non bloquant) : **secret séparé lecture (`inventory`) vs destructif
   (`treatment`/`delete`)** pour un moindre-privilège plus fin.
4. **Déploiement** : provisionner `ZELIAN_PROVISIONING_SECRET` (≥ 256 bits) ; stack Plane isolée
   (Postgres/Redis/RabbitMQ/MinIO) hors cluster Supabase Insider.

---

## 9. RÉFÉRENCES

- **Mémoire** (auto-chargée) : `MEMORY.md` → `plane-provisioning-module.md`,
  `plane-bascule-interne-prive.md` (doctrine repo séparé), `plane-fork-reimplemente-features-payantes.md`,
  `zelian-insider-repos-et-conventions.md`, `zelian-insider-repo-regles.md`, `plane-repo-lives-in-wsl.md`.
- **PRs** : #106 (US-01→05, mergée), #107 (US-06, mergée le 2026-07-22) sur `userLinpy/plane` → `preview`.
- **Commits (branche `feat/provisioning-zelian-v1`)** : `14cecbc67c` (feat US-06), `112050493d` (docs),
  `695a200a01` (compass), `0b5aa96f42` (durcissement post-revue), `2e440f05ef`+ (ce handoff). **Merges** :
  #106 = `230b8f27e7`, #107 = `46aec5e09b`.

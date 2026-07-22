# Spec Technique — Provisioning annuaire Zelian → Plane

> **Module** : `api/provisioning-zelian`
> **Statut** : v0.2.0 — US-01→05 (provisioning) **et** US-06 (traitement des traces) implémentés.
> **Mis à jour** : 2026-07-22 (après implémentation US-06).

## Architecture

Deux familles d'endpoints **server-to-server** dans un **namespace `zelian` dédié** (comme le SSO),
appelées par le service de synchronisation Zelian (Manage), jamais par un humain. Toutes partagent
une **habilitation unique** par secret de service, isolée dans un mixin pour minimiser la divergence
avec l'upstream et la surface d'attaque.

- US-01→05 (provisioning) : `POST /api/zelian/provisioning/`.
- US-06 (traces) : `POST /api/zelian/traces/inventory/` (lecture) + `POST /api/zelian/traces/treatment/`
  (mutation).

**Zéro migration** (ADR-002) : réutilise `User`, `WorkspaceMember`, `ProjectMember`, `Project`,
`Issue`, `IssueComment`, `IssueActivity`, `Page`, `FileAsset`, `Cycle`, `IssueView`. Aucun nouveau modèle,
aucun nouveau champ (le tombstone réutilise `User.masked_at`, champ dormant de la migration 0096).

### Habilitation partagée — `app/views/zelian/_base.ZelianServiceAuthMixin`

- Secret de service partagé en en-tête `X-Zelian-Provisioning-Key`, lu via `get_configuration_value`
  (config instance chiffrable / fallback env — gestionnaire de secrets, règle 07 Insider), comparé en
  **temps constant** (`hmac.compare_digest`). **Fail-closed** : sans secret configuré, tout est refusé
  (403). Convention Insider `09-architecture-auth §9.11` / `HUB-TOOLS-ANALYSE §8.7` — jamais le JWT
  identité ni un token utilisateur (Plane est un outil tiers).
- `authentication_classes = []`, `permission_classes = [AllowAny]`, **`throttle_classes = []`** : ces
  endpoints machine sont exemptés du throttle anonyme DRF (`anon: 30/minute`, `settings/common.py`) qui
  brimerait à tort les lots légitimes de Manage (offboarding en masse) ; la garde est le **secret**, pas
  l'IP, et un secret erroné est déjà rejeté (403) avant tout traitement.

### US-01→05 — provisioning (endpoint `provisioning/`)

Traitement par lot (`members`), un élément par transaction indépendante (un élément fautif ne casse pas
le lot — cas 9). Logique par élément :

| Action | Comportement |
|---|---|
| `provision` (défaut) | `User` créé si absent (`is_password_autoset=True`, entrée par SSO) sinon **adopté** (jointure email, RM-02) ; `WorkspaceMember` créé (défaut **Membre 15**, RM-03) ou réactivé ; rôle **transmis = appliqué / omis = inchangé** (RM-04) ; passage à **Invité (5)** ⇒ cascade `ProjectMember` (RM-14, RETRO-011). **Companion US-06** : recoche d'un compte marqué « Profil supprimé » (`masked_at` non nul) ⇒ tombstone levé, identité restaurée depuis `name`, `masked_at`/`last_logout_time` remis à null. |
| `deactivate` | `WorkspaceMember.is_active = False` — réversible, aucune donnée supprimée (RM-05). |

Gardes : jamais un compte `is_bot` ni `is_superuser` (RM-09, statut `protected`) ; idempotent (RM-10,
`unchanged`) ; silencieux — aucun email (RM-11) ; appelant non habilité rejeté sans effet (RM-13).

### US-06 — traitement des traces (endpoints `traces/`)

Le dialogue UI vit dans **Manage** (hors scope Plane) ; Plane ne fournit que les services. Gardes
communes (les deux endpoints) : appelant non habilité → 403 (RM-13) ; personne inconnue → `unknown`
no-op sans erreur (cas 6) ; compte hors annuaire (`is_bot`/`is_superuser`) → `protected` (RM-09).

**Inventaire** (`traces/inventory/`, lecture) — `{workspace_slug, email}` → projets créés (avec drapeau
`shared`/`recommend_legation` = présence de contributions/membres actifs d'autrui) + compteurs
issues/commentaires/pages/pièces jointes. Alimente le dialogue de Manage (cas 8 : avertir sur un projet
partagé).

**Traitement** (`traces/treatment/`) — `{workspace_slug, email, account, keep_name, categories, projects[]}`.
Enveloppe `transaction.atomic()` + `select_for_update()` sur la personne (sérialise deux syncs
concurrentes — cas 10). Ordre : **légations d'abord** (sauvent le partagé) → purges de projets →
traitement du compte (`mask` ⇒ tombstone RM-07 ; `delete` ⇒ purge totale). Compte-rendu par opération.

**Purge définitive (RM-06)** — jamais un simple soft-delete (qui ne pose que `deleted_at` et laisse la
ligne visible via `all_objects` jusqu'au `hard_delete()` planifié). On supprime **en dur** :
`Model.all_objects.filter(...).delete()` (manager nu ⇒ vrai `DELETE`) ou `instance.delete(soft=False)`.
Landmine désamorcée : `IssueActivity.issue`/`.issue_comment` sont `on_delete=DO_NOTHING` — au grain
unitaire (issue/commentaire), on supprime d'abord les `IssueActivity` liées, sinon échec de contrainte
FK au COMMIT (FK Postgres `DEFERRABLE INITIALLY DEFERRED`). Au grain projet, la cascade via
`IssueActivity.project` suffit. Clés S3 des `FileAsset` collectées avant suppression (voir effacement S3).

**Projet partagé (cas 8)** — un `traces/treatment` `action:"purge"` sur un projet contenant des
contributions/membres actifs d'autrui **ne détruit pas** le projet en entier (`purged_own_only`) : seuls
les items de la personne sont purgés. La légation est la voie recommandée pour transférer un projet
partagé.

**Légation (RM-08 / cas 7)** — repreneur = `WorkspaceMember` actif requis (sinon `rejected /
successor_not_active_member`). Le repreneur devient `ProjectMember` **ADMIN actif** ; repoints via
`.update()` (jamais `.save()`, qui écraserait `updated_by` en contexte server-to-server) :
`project_lead` → repreneur, `default_assignee` → repreneur s'il pointait la personne, `Cycle`/`IssueView`/
`Page` `owned_by` du projet → repreneur. Contenu intact ⇒ le projet ne dépend plus de la personne.

**Marquage « Profil supprimé — [Nom] » (RM-07, `account:"mask"`)** — `User.objects.filter(pk).update(...)`
(bypass `User.save()`) : `display_name`/`first_name`/`last_name` écrasés en tombstone (affichage partout
via `UserLiteSerializer`, **sans nouvelle surface front**), `avatar=""`, `avatar_asset=None`,
`masked_at=now()` (marqueur d'idempotence — RM-10), `is_active=False` **et** `last_logout_time=now()`
(gate GHSA `authentication/adapter/base.py:326` : bloque toute réactivation au login SSO). Memberships
désactivés.

**Suppression du compte (RM-06, `account:"delete"`, purge totale)** — ordre strict :
1. **Refus** amont si `Workspace.objects.filter(owner=user).exists()` (`rejected / owns_workspace`) : ne
   jamais risquer de supprimer un workspace en cascade.
2. Purge de tout le contenu structuré de la personne (issues/commentaires/pages), + **toutes** ses
   `FileAsset` (clés S3 collectées).
3. Neutralisation des liens CASCADE vers des conteneurs d'autrui : `project_lead`/`default_assignee` →
   `NULL` (nullables) ; `Cycle.owned_by`/`IssueView.owned_by` (non-nullables) → réattribués à
   `workspace.owner`.
4. `user.delete()` (`User` n'est **pas** un `SoftDeleteModel` : suppression dure directe ; la cascade
   n'emporte plus que ses propres données — profil, préférences, tokens, sessions, memberships).
   ⇒ Recoche ultérieure (US-01) recrée un membre **vierge** (nouveau `User`), les données purgées ne
   réapparaissent jamais.

**Effacement physique S3/MinIO (RGPD droit à l'effacement)** — les clés d'objets (`FileAsset.asset`) sont
collectées pendant la transaction, puis supprimées via `S3Storage().delete_files(...)` (pattern existant
`authentication/adapter/base.py:262`) enregistré en **`transaction.on_commit`** : découplé et
best-effort, une indispo S3 ne casse ni ne partialise la purge BDD (déjà définitive).

## Décisions techniques (politique ADR : `spec-technique.md`, pas d'ADR)

- **Purge = hard delete** (`soft=False` / `all_objects`), jamais soft (RM-06 « définitif immédiat »).
- **Purge totale supprime la ligne `User`** (spec US-06 §6-7 « aucune trace / membre vierge »),
  encadrée par les gardes cascade ci-dessus. Anonymisation-conservation écartée (laisserait l'email en
  résidu).
- **Tombstone via champs display + `masked_at`** (champ dormant réutilisé) — zéro migration, zéro
  nouvelle surface front.
- **Endpoints machine exemptés du throttle anonyme** — garde = secret, pas IP.
- **Effacement S3 post-commit découplé** — conformité RGPD sans coupler la purge BDD à la dispo S3.

(Aucune de ces décisions ne passe la checklist ADR — confinées au module ; cf. `06-adr-policy.md` Q3.)

## Fichiers

**Créés** :
- `apps/api/plane/app/views/zelian/_base.py` — `ZelianServiceAuthMixin` (auth secret partagée + exemption throttle).
- `apps/api/plane/app/views/zelian/traces.py` — `ZelianTracesInventoryEndpoint`, `ZelianTracesTreatmentEndpoint` + helpers purge/légation/tombstone/suppression.
- `apps/api/plane/tests/contract/app/test_zelian_traces_app.py` — 25 tests de contrat.

**Modifiés** :
- `apps/api/plane/app/views/zelian/provisioning.py` — hérite du mixin (auth dédupliquée) ; companion « dé-tombstone » dans `_provision`.
- `apps/api/plane/app/views/zelian/__init__.py` — exports traces.
- `apps/api/plane/app/urls/zelian.py` — routes `traces/inventory/` et `traces/treatment/`.

## API

`POST /api/zelian/traces/inventory/` — en-tête `X-Zelian-Provisioning-Key`.
- Corps : `{ "workspace_slug": "zelian", "email": "prenom.nom@zelian.fr" }`.
- 200 : `{ email, status: found|unknown|protected, user_id, totals{projects_created,issues_created,comments,pages,attachments}, projects[{project_id,name,identifier,created_by_departing,counts,shared,other_active_members,recommend_legation}] }`.

`POST /api/zelian/traces/treatment/` — en-tête `X-Zelian-Provisioning-Key`.
- Corps :
  ```json
  { "workspace_slug": "zelian", "email": "...",
    "account": "mask|delete", "keep_name": true,
    "categories": {"issues": false, "comments": false, "pages": false, "attachments": false},
    "projects": [ {"project_id": "...", "action": "purge|legate|keep", "successor_email": "..."} ] }
  ```
- 200 : `{ email, status, account: masked|deleted|unchanged, reason?, operations[{op,result,...}], summary }`.
- Codes : `403` (non habilité / non configuré), `400` (`workspace_slug`/`email`/`account` invalides), `404` (workspace inconnu).

## Config

- `ZELIAN_PROVISIONING_SECRET` — secret de service partagé (gestionnaire de secrets / env), commun aux
  endpoints provisioning et traces. Non défini ⇒ endpoints désactivés (fail-closed). **Aucune nouvelle
  variable pour US-06.**

## Tests / vérification (2026-07-22, conteneur `plane-api-1`)

- **25 tests de contrat** `test_zelian_traces_app.py` : inventaire (unknown/compteurs/`shared`/protégé/
  auth), purge **définitive via `all_objects`**, landmine `IssueActivity` sans `IntegrityError`, projet
  partagé + workspace préservés (cas 8), suppression compte + **recoche vierge** + refus `owns_workspace`
  + neutralisation `project_lead`/`Cycle`/`IssueView`, légation + refus repreneur inactif (cas 7),
  tombstone + companion dé-tombstone, silence (`mail.outbox`), effacement S3 (mock + `on_commit`).
- **Régression** : 15 tests provisioning verts (suite combinée 40 verte après exemption throttle).
- `makemigrations --check --dry-run` : **No changes detected** (zéro migration). `ruff check` (0.9.7 et
  latest) : All checks passed. En-tête copyright conforme à `COPYRIGHT.txt`.

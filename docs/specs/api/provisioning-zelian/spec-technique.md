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
  (403).
- **Conformité doctrine (nuancée)** : la méthode **s'inspire de** `09-architecture-auth §9.11`
  (server-to-server *limité, journalisé, secret dans le gestionnaire de secrets*, règle 07 — respecté)
  et l'**adapte**. `HUB-TOOLS-ANALYSE §8.7` prescrit « **service account + token API scoppé** » pour
  *lire* Plane (mode C) ; mais provisionner des membres / purger / supprimer un compte est **hors
  surface API** de Plane (extension ORM « isolée et publiable », sanctionnée par la doctrine
  outil-externe / repo séparé) → un **secret de service instance-level** est le mécanisme adéquat, pas
  un token utilisateur scoppé. Jamais le JWT identité (ES256, humains) ni un token utilisateur. *Point
  ouvert* : moindre-privilège plus fin (secret lecture vs destructif) possible en durcissement ultérieur.
- `authentication_classes = []`, `permission_classes = [AllowAny]` (endpoints machine) ;
  `throttle_classes = [ZelianFailedAuthThrottle]` : throttle **uniquement les échecs** d'auth (secret
  absent/faux, `30/min` par IP) — un appel au **bon secret n'est jamais bridé** (offboarding en masse),
  ce qui borne la devinette de secret sans pénaliser Manage. (Le throttle anonyme global de
  `settings/common.py` est par IP → inadapté à un endpoint machine ; on ne l'utilise pas.)

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

**Self-FK `parent` (Issue/IssueComment/Page) = `CASCADE`** — piège majeur : hard-deleter un parent
emporterait les sous-issues/réponses/sous-pages d'**autrui** (destruction + `IssueActivity` orphelin →
IntegrityError). `_own_closure_detach_foreign` **détache** (`parent=None`) les descendants d'autrui
(préservés) et **étend** la suppression aux descendants **propres** (activités nettoyées sur la fermeture
complète) ; les pages détachent leurs enfants avant `delete(soft=False)`. Fermeture bornée (chaque nœud
enfilé une seule fois — sûr même sur un cycle de `parent`).

**Projet partagé (cas 8)** — `_is_shared` (conservateur) détecte tout membre (actif **ou non**), issue,
commentaire, page, cycle, vue, module ou pièce jointe d'**autrui**. Un `action:"purge"` sur un projet
partagé **ne détruit pas** le projet en entier (`purged_own_only`) : seuls les items de la personne sont
purgés. La légation est la voie recommandée pour transférer un projet partagé.

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
1. **Refus** amont si `Workspace.all_objects.filter(owner=user)` (`rejected / owns_workspace`) —
   `all_objects` couvre aussi un workspace **soft-deleted** (que la cascade détruirait quand même).
2. Purge du contenu propre dans le workspace visé (issues/commentaires/**pages partagées préservées**).
3. Pièces jointes : **réattribuées** dans les projets légués (projet intact), **purgées** (S3 inclus)
   ailleurs — sinon la cascade `FileAsset.user` (CASCADE) les emporterait sans effacer S3.
4. Purge **GLOBALE** landmine-safe de ses commentaires (tous workspaces) : sans ça, `user.delete()`
   cascade les commentaires hors-workspace → `IssueActivity` orphelins → IntegrityError.
5. Neutralisation **GLOBALE** de TOUT conteneur partagé CASCADE→User : `project_lead`/`default_assignee`
   → `NULL` ; `Cycle`/`IssueView`/`Page`/`PageVersion`/`IssueVersion`/`IssueDescriptionVersion.owned_by`
   et `WorkspaceTheme`/`WorkspaceIntegration`/`GithubRepositorySync.actor` → owner du workspace.
6. `user.delete()` (`User` n'est **pas** un `SoftDeleteModel` : suppression dure directe ; la cascade
   n'emporte plus que ses données propres — profil, préférences, tokens, sessions, memberships,
   réactions, favoris, liens perso). Issues d'autres workspaces survivent (`created_by` SET_NULL).
   ⇒ Recoche ultérieure (US-01) recrée un membre **vierge** (nouveau `User`).

*Compromis documentés (sûrs, non destructifs — validés par double revue adversariale)* : réattribution
cross-workspace vers l'owner du workspace visé (une purge par workspace serait plus fine) ; une
sous-issue **propre** nichée sous un nœud d'**autrui** est préservée (détachée) plutôt que purgée
(doctrine « préserver plutôt que détruire »).

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
- **Throttle sur les échecs d'auth uniquement** (`ZelianFailedAuthThrottle`, bon secret jamais bridé) —
  garde = secret, pas IP ; borne la devinette sans pénaliser Manage (offboarding en masse).
- **Préserver le contenu d'autrui à tous les grains** (cas 8 généralisé) : jamais détruire une
  sous-issue / réponse / sous-page / cycle / vue / page d'autrui ; conteneurs partagés réattribués,
  descendants d'autrui détachés — hard-delete réservé au contenu strictement propre.
- **Effacement S3 post-commit découplé** — conformité RGPD sans coupler la purge BDD à la dispo S3.

(Aucune de ces décisions ne passe la checklist ADR — confinées au module ; cf. `06-adr-policy.md` Q3.)

## Fichiers

**Créés** :
- `apps/api/plane/app/views/zelian/_base.py` — `ZelianServiceAuthMixin` (auth secret partagée + exemption throttle).
- `apps/api/plane/app/views/zelian/traces.py` — `ZelianTracesInventoryEndpoint`, `ZelianTracesTreatmentEndpoint` + helpers purge/légation/tombstone/suppression.
- `apps/api/plane/tests/contract/app/test_zelian_traces_app.py` — 35 tests de contrat.

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

- **35 tests de contrat** `test_zelian_traces_app.py` : inventaire (unknown/compteurs/`shared`/protégé/
  auth), purge **définitive via `all_objects`**, landmine `IssueActivity` sans `IntegrityError`, projet
  partagé + workspace préservés (cas 8), **page partagée préservée** (réattribuée), **sous-issue /
  réponse / sous-page d'autrui préservées** (parent-cascade), suppression compte + **recoche vierge** +
  refus `owns_workspace` (y c. workspace **soft-deleted**) + neutralisation cascade, **delete
  multi-workspace sans crash**, légation + refus repreneur inactif (cas 7), tombstone + companion
  dé-tombstone, throttle (bon secret jamais bridé), silence (`mail.outbox`), effacement S3.
- **Régression** : 15 tests provisioning verts (suite combinée **50** verte).
- **Double revue adversariale** (workflow 4 relecteurs + re-vérif) : 12 défauts corrigés (dont 4
  blockers parent-cascade / crash), re-vérif sans blocker ni majeur résiduel.
- `makemigrations --check --dry-run` : **No changes detected** (zéro migration). `ruff check` (0.9.7 et
  latest) : All checks passed. En-tête copyright conforme à `COPYRIGHT.txt`.

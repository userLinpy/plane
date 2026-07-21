# Spec Fonctionnelle — Provisioning annuaire Zelian → Plane

| Champ          | Valeur                                              |
|----------------|-----------------------------------------------------|
| Module         | api/provisioning-zelian                             |
| Version        | 0.1 (init)                                          |
| Date           | 2026-07-21                                          |
| Auteur         | Lucie                                               |
| Rédaction      | Dev + Claude (Phase 3 Zelian, session Claude Code)  |
| Dépendances    | api/sso-zelian, api/workspaces                      |

---

## ADRs

| ADR | Titre | Statut |
|-----|-------|--------|
| [ADR-001](../../../adr/ADR-001-reimplementation-ce-clean-room.md) | Réimplémentation CE clean-room | Accepté |
| [ADR-002](../../../adr/ADR-002-schema-dormant-zero-migration.md) | Schéma dormant / zéro migration | Accepté |
| [ADR-003](../../../adr/ADR-003-is-zelian-enabled-bascule-interne-externe.md) | `IS_ZELIAN_ENABLED` — bascule interne/externe | Accepté |

> Table renseignée à la main (bug connu `adr-linker --find` : ne remonte que `Features: *`).
> Contexte utile : RETRO-011 (cascade Invité), RETRO-012 (sécurisation invitations), RETRO-021 (RBAC 20/15/5).

---

## Contexte métier

Aujourd'hui, une personne n'existe dans Plane qu'après une invitation manuelle **et** sa
première connexion — avant cela elle n'est ni cherchable ni assignable, ce qui casse
l'objectif « présente et assignable dès son arrivée ». Ce module fait de l'app **Manage**
le poste de pilotage des accès Plane : une case « accès Plane » par personne de
l'annuaire Zelian. Plane devient une **réplique** de ces décisions (sens unique
annuaire → Plane, jointure par **email**) : cocher provisionne, décocher désactive,
supprimer le compte déclenche un traitement des traces. Aucune invitation, aucune
saisie manuelle d'email côté Plane.

---

## Objectifs fonctionnels

**Objectif principal :**
Toute personne cochée dans Manage est membre de l'espace de travail `zelian`, cherchable
et assignable, avant même sa première connexion.

**Objectifs secondaires :**
- Retrait d'accès immédiat et réversible (décoche), sans perte d'historique.
- Traitement des traces à la suppression d'un compte : conservation marquée, purge
  sélective ou totale, légation de projet (esprit RGPD — droit à l'effacement).
- Gestion du rôle Plane depuis Manage, avec un contrat simple qui n'écrase jamais
  par surprise.

---

## Utilisateurs concernés / Personas

| Persona | Niveau technique | Fréquence d'usage | Permissions clés |
|---------|------------------|-------------------|------------------|
| Super-admin / admin technique Zelian | Élevé | Ponctuel (arrivées, départs) | Coche/décoche, choix du rôle, suppression + traitement des traces (dans Manage) — élargissement RH à reconfirmer |
| Collaborateur provisionné | Variable | Quotidien | Subit le provisioning ; entre par le badge Zelian (SSO) ; utilise Plane selon son rôle |
| Système appelant (Manage / intermédiaire de synchronisation) | — | Automatique | Seul canal habilité à écrire les décisions d'accès dans Plane |

---

## Parcours utilisateur / User Stories

### US-01 — Accorder l'accès (coche)
> **En tant que** super-admin,
> **je veux** cocher « accès Plane » sur la fiche d'une personne dans Manage,
> **afin de** la rendre membre, cherchable et assignable, sans invitation ni action de sa part.

**Priorité :** Must have

**Parcours détaillé :**
1. Le super-admin ouvre la fiche de la personne dans Manage et coche « accès Plane » (rôle proposé par défaut : Membre).
2. Manage transmet la décision à Plane (email, identité, rôle).
3. Plane crée le compte s'il n'existe pas (sans mot de passe — l'entrée se fera par le badge Zelian), ou **adopte** le compte existant portant cet email.
4. Plane rattache la personne comme membre actif de l'espace `zelian` avec le rôle transmis.
5. La personne apparaît immédiatement dans les recherches de membres et peut être assignée. Aucun email n'est envoyé.

### US-02 — Arriver dans Plane (collaborateur provisionné)
> **En tant que** collaborateur coché,
> **je veux** entrer dans Plane via le badge Zelian,
> **afin d'** accéder directement à l'espace de travail sans étape d'invitation.

**Priorité :** Must have

**Parcours détaillé :**
1. Le collaborateur ouvre Plane et entre via le badge Zelian (connexion sans mot de passe Plane).
2. Plane le reconnaît par son email : il est déjà membre — aucun écran « invitation requise ».
3. Il retrouve les éléments qui lui ont éventuellement déjà été assignés avant sa première connexion.

### US-03 — Retirer l'accès (décoche)
> **En tant que** super-admin,
> **je veux** décocher « accès Plane »,
> **afin de** couper l'accès de la personne sans rien perdre de son historique.

**Priorité :** Must have

**Parcours détaillé :**
1. Le super-admin décoche la case sur la fiche de la personne ; dans Manage, son rôle Plane s'affiche « Aucun ».
2. Manage transmet la décision à Plane.
3. Plane désactive le membre : l'accès est refusé au prochain contrôle. Profil, contributions, assignations et mentions restent intacts et visibles normalement.

### US-04 — Ré-accorder l'accès (recoche)
> **En tant que** super-admin,
> **je veux** recocher une personne précédemment décochée,
> **afin de** restaurer son accès à l'identique.

**Priorité :** Must have

**Parcours détaillé :**
1. Le super-admin recoche la case (rôle proposé : celui transmis par Manage, défaut Membre).
2. Plane réactive le membre existant : il retrouve son profil, son historique et ses assignations tels quels.

### US-05 — Gérer le rôle depuis Manage
> **En tant que** super-admin,
> **je veux** définir le rôle Plane (Admin / Membre / Invité) depuis Manage,
> **afin de** garder la cohérence entre l'annuaire et Plane selon le mode choisi.

**Priorité :** Must have

**Parcours détaillé :**
1. Dans Manage, la coche s'accompagne d'un sélecteur de rôle (défaut : Membre).
2. Manage choisit, selon son réglage interne (« Plane a le droit de changer les rôles » — réglage côté Manage, hors périmètre Plane), d'inclure ou non le rôle dans chaque synchronisation.
3. Contrat côté Plane, sans exception : **rôle transmis = appliqué** (à la création comme en mise à jour) ; **rôle omis = inchangé**.
4. Pour s'aligner sur d'éventuels changements faits dans Plane, Manage lit les rôles courants via la consultation des membres existante (lecture seule).
5. Un passage au rôle Invité entraîne la rétrogradation à Invité sur tous les projets de la personne (invariant existant — RETRO-011).

### US-06 — Supprimer le compte et traiter les traces
> **En tant que** super-admin,
> **je veux** qu'à la suppression du compte Zelian d'une personne, Manage me propose de traiter ses traces dans Plane,
> **afin de** choisir entre conservation marquée, purge sélective/totale et légation de projet.

**Priorité :** Should have (v1.1 — spécifié complet, implémenté après US-01→05)

**Parcours détaillé :**
1. Le super-admin supprime le compte de la personne dans Manage ; la case Plane se décoche et l'accès est coupé (comme US-03).
2. Manage affiche un dialogue « supprimer aussi ses traces sur Plane ? » alimenté par l'**inventaire** fourni par Plane : projets créés, work items créés, commentaires, pages créées, pièces jointes déposées (tout contenu visible).
3. Le super-admin sélectionne les éléments à supprimer, un par un ou « tout sélectionner ».
4. Pour un projet important, il peut choisir « léguer » : le projet est transféré à un repreneur (membre actif) au lieu d'être supprimé.
5. S'il choisit de **laisser les traces** : les contributions restent, et le profil de la personne s'affiche « Profil supprimé — [Nom] » (au survol et partout où l'auteur apparaît).
6. S'il purge : les éléments sélectionnés disparaissent définitivement. Si tout est purgé, il ne reste aucune trace de la personne dans Plane.
7. Recocher la personne plus tard recrée un membre **vierge** : les données purgées ne réapparaissent jamais.

---

## Règles métier

- **RM-01** — La case « accès Plane » de Manage est l'**unique** voie d'attribution d'accès : pas d'invitation Plane, pas d'entrée automatique à la connexion, pas d'auto-inscription.
- **RM-02** — La jointure se fait par **email** : un email = un compte. Un compte existant portant l'email est adopté ; aucun doublon n'est jamais créé.
- **RM-03** — Rôle par défaut à la création : **Membre (15)**.
- **RM-04** — Rôle transmis = appliqué (création et mise à jour) ; rôle omis = inchangé. Aucune autre règle de fusion.
- **RM-05** — Décocher **désactive** (accès refusé au prochain contrôle) et ne supprime **rien** ; l'opération est réversible à l'identique.
- **RM-06** — La purge est **définitive** : un élément purgé ne réapparaît jamais, y compris si la personne est recochée ensuite (repart de zéro).
- **RM-07** — Après suppression avec conservation des traces, l'auteur s'affiche « **Profil supprimé — [Nom]** » sur toutes ses contributions.
- **RM-08** — Un projet peut être **légué** à un repreneur membre actif au lieu d'être supprimé ; le projet et son contenu restent intacts.
- **RM-09** — Le provisioning ne gère **que** les identités issues de l'annuaire Zelian : il ne modifie, ne désactive ni ne purge jamais un compte hors annuaire (comptes de service, super-admin Plane).
- **RM-10** — Toutes les opérations sont **idempotentes** : rejouer une même décision ne change pas l'état final.
- **RM-11** — Le provisioning est **silencieux** : Plane n'envoie aucun email ni notification aux personnes concernées.
- **RM-12** — Seuls le super-admin et les admins techniques manipulent coche, rôle et suppression (élargissement RH : à reconfirmer).
- **RM-13** — Toute demande de provisioning provenant d'un appelant non habilité est rejetée sans effet.
- **RM-14** — Un passage au rôle Invité déclenche la cascade Invité sur les projets (cohérence avec l'invariant existant RETRO-011).

---

## Cas limites et edge cases

| # | Cas | Comportement attendu |
|---|-----|----------------------|
| 1 | Recocher une personne déjà active | Aucun changement (idempotence, RM-10) |
| 2 | Cocher un email déjà présent dans Plane (compte créé nativement avant) | Adoption du compte : il devient membre, historique conservé, pas de doublon (RM-02) |
| 3 | Décocher pendant que la personne a Plane ouvert | Accès coupé au prochain contrôle ; pas de déconnexion forcée immédiate en v1 |
| 4 | Rôle modifié dans Plane, puis synchronisation **sans** rôle | Rôle Plane inchangé (RM-04) |
| 5 | Rôle modifié dans Plane, puis synchronisation **avec** rôle | Rôle transmis appliqué (RM-04) |
| 6 | Suppression d'une personne jamais provisionnée | Aucune action côté Plane, aucune erreur |
| 7 | Légation à une personne non membre ou désactivée | Refusée ; le repreneur doit être un membre actif |
| 8 | Purge d'un projet contenant des contributions d'autres personnes | La suppression du projet emporte tout son contenu, y compris celui d'autrui — le dialogue l'annonce explicitement ; la légation est la voie recommandée pour un projet partagé |
| 9 | Élément de demande invalide (email malformé, rôle inconnu) dans un lot | L'élément est rejeté avec compte-rendu ; les autres éléments du lot sont traités |
| 10 | Deux synchronisations simultanées | État final cohérent, identique à une exécution séquentielle (RM-10) |

---

## Contraintes fonctionnelles

- **Réglementaires :** RGPD — droit à l'effacement (US-06 : purge définitive, conservation marquée) ; minimisation (seules les personnes cochées existent dans Plane).
- **UX :** une coche est effective en quelques secondes ; l'inventaire des traces est complet (aucune contribution visible omise) ; la purge définitive et l'emport du contenu d'autrui (cas 8) sont annoncés avant confirmation.
- **Accessibilité :** aucune nouvelle surface utilisateur dans Plane en v1 (le dialogue vit dans Manage) ; le marquage « Profil supprimé » respecte l'affichage standard des profils Plane.
- **Performance :** un lot de l'ordre de l'effectif de l'entreprise (quelques centaines de personnes) est traité en un appel.

---

## Critères d'acceptation

- [ ] Cocher une personne inconnue de Plane crée compte + membre actif : elle est cherchable et assignable, sans qu'aucun email ne parte.
- [ ] Cocher un email déjà existant adopte le compte sans créer de doublon.
- [ ] Une personne cochée entre par le badge Zelian sans écran « invitation requise », avant même toute première connexion.
- [ ] Décocher refuse l'accès au prochain contrôle et ne modifie ni profil, ni contributions, ni assignations.
- [ ] Recocher restaure l'accès à l'identique (profil, historique, assignations).
- [ ] Sans rôle précisé, un nouveau membre est Membre (15) ; un rôle transmis est appliqué ; un rôle omis laisse l'existant intact.
- [ ] Un passage au rôle Invité rétrograde la personne à Invité sur tous ses projets.
- [ ] Après suppression avec conservation, chaque contribution affiche « Profil supprimé — [Nom] ».
- [ ] Une purge sélective supprime exactement les éléments choisis et ne touche pas les autres.
- [ ] Après purge totale puis recoche, le membre repart vierge : rien de purgé ne réapparaît.
- [ ] Une légation transfère le projet intact au repreneur (membre actif requis).
- [ ] Une demande d'un appelant non habilité est rejetée sans aucun effet.
- [ ] Rejouer une même demande (coche, décoche, rôle) ne change pas l'état final.
- [ ] Les comptes hors annuaire (service, super-admin Plane) ne sont jamais affectés par le provisioning.

---

## Ce qui est HORS scope

- **Workflow de suggestion de rôle côté Plane** (mode « seul Manage décide » : Plane propose, un admin accepte) — reporté, à spécifier dans une future itération.
- **Sélection de projets dans Manage** (provisionner aussi l'appartenance aux projets) — reporté ; évolution envisagée, peut être abandonnée ou améliorée. En v1 le provisioning s'arrête à l'espace de travail ; l'ajout aux projets reste un geste manuel dans Plane.
- **Auto-inscription aux projets publics** — non retenu en v1.
- **Notifications aux personnes provisionnées** — le provisioning est silencieux (RM-11) ; l'information passe par l'accueil Zelian.
- **Écriture Plane → annuaire** — jamais : Manage lit Plane, Plane n'écrit pas dans l'annuaire.
- **Resynchronisation des noms/avatars après création** — le profil Plane appartient ensuite à l'utilisateur.
- **Changement d'email dans l'annuaire** — non géré en v1 (la jointure email est stable).
- **Déconnexion forcée immédiate à la décoche** — v1 se contente du refus au prochain contrôle.
- **L'interface de Manage elle-même** (case, sélecteur, toggle, dialogue de suppression) — spécifiée côté app Manage ; ce module couvre les services rendus par Plane.

---

## Dépendances fonctionnelles

- [`api/sso-zelian`] — l'entrée des personnes provisionnées se fait par le badge Zelian ; la reconnaissance repose sur la même jointure email.
- [`api/workspaces`] — le provisioning écrit l'appartenance et les rôles (Admin 20 / Membre 15 / Invité 5) dans l'espace de travail `zelian` ; la cascade Invité (RETRO-011) s'applique.

> **ADRs à créer avant Phase 4 :** Aucun — tous les ADRs nécessaires existent (les décisions restantes — mécanisme d'habilitation de l'appelant, sémantique de lot, inventaire — relèvent de `spec-technique.md`, rejet politique v2.3.0).

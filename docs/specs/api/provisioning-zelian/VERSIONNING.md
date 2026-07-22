# VERSIONNING — api/provisioning-zelian

| Version | Date | Type | Description | Fichiers touchés |
| ------- | ---- | ---- | ----------- | ---------------- |
| 0.1.0 | 2026-07-21 | feat | Endpoint de provisioning server-to-server `POST /api/zelian/provisioning/` (US-01→05). Secret de service (`X-Zelian-Provisioning-Key`, `get_configuration_value`, `compare_digest`, fail-closed). Lot avec compte-rendu par élément ; création/adoption `User`+`WorkspaceMember`, contrat rôle (transmis=appliqué/omis=intact, défaut Membre 15), désactivation réversible, cascade Invité, gardes bot/super-admin, idempotent, silencieux. Zéro migration. Vérifié : 15 tests de contrat + régression SSO 15. US-06 (purge/légation) non implémenté (v1.1). | `apps/api/plane/app/views/zelian/{provisioning.py, __init__.py}`, `apps/api/plane/app/urls/{zelian.py, __init__.py}`, `apps/api/plane/tests/contract/app/test_zelian_provisioning_app.py`, `docs/specs/api/provisioning-zelian/{spec-technique.md, tech-design.md}` |

> Table mise à jour par @update-writer-after-implement après chaque implémentation.

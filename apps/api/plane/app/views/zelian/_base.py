# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Auth server-to-server partagée par les endpoints Zelian (provisioning + traces).
# Convention Insider §9.11 / §8.7, règle 07 — secret de service, jamais le JWT identité
# ni un token utilisateur.

import hmac
import os

from rest_framework.permissions import AllowAny
from rest_framework.throttling import SimpleRateThrottle

from plane.license.utils.instance_value import get_configuration_value


def _secret_matches(request):
    """Vrai si l'en-tête porte le secret de service configuré (comparaison temps constant).

    **Fail-closed** : sans secret configuré, retourne toujours Faux (règle 07 : le secret
    vit dans le gestionnaire de secrets / env, jamais dans le repo).
    """
    (secret,) = get_configuration_value(
        [
            {
                "key": "ZELIAN_PROVISIONING_SECRET",
                "default": os.environ.get("ZELIAN_PROVISIONING_SECRET"),
            }
        ]
    )
    if not secret:
        return False
    provided = request.headers.get("X-Zelian-Provisioning-Key", "")
    return hmac.compare_digest(
        provided.encode("utf-8"), str(secret).encode("utf-8")
    )


class ZelianFailedAuthThrottle(SimpleRateThrottle):
    """Throttle **uniquement les échecs d'auth** (secret absent ou faux), par IP.

    Les appels au bon secret ne sont **jamais** bridés — Manage peut enchaîner un
    offboarding en masse sans être limité par le throttle anonyme DRF. En revanche, un
    appelant sans le bon secret est borné à quelques tentatives/minute : ça garde une
    défense contre la devinette du secret sur cet endpoint ``AllowAny``, sans pénaliser
    l'usage légitime (moindre-privilège pragmatique).
    """

    scope = "zelian_failed_auth"
    rate = "30/min"

    def get_cache_key(self, request, view):
        # Bon secret -> None => SimpleRateThrottle ne compte pas la requête (jamais throttlé).
        if _secret_matches(request):
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": self.get_ident(request),
        }


class ZelianServiceAuthMixin:
    """Habilitation server-to-server par secret de service partagé.

    Le secret est fourni en en-tête ``X-Zelian-Provisioning-Key``, lu via
    ``get_configuration_value`` (config instance chiffrable / fallback env — gestionnaire de
    secrets, règle 07 Insider) et comparé en **temps constant**. **Fail-closed** : sans secret
    configuré, la porte reste fermée pour tout le monde (RM-13).

    Utilisé par ``ZelianProvisioningEndpoint`` (US-01→05) et les endpoints ``traces`` (US-06),
    tous deux appelés par le service de synchronisation Zelian (Manage), pas par un humain.
    Le throttle ne s'applique qu'aux **échecs** d'auth (voir ``ZelianFailedAuthThrottle``).
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ZelianFailedAuthThrottle]

    def _is_authorized(self, request):
        return _secret_matches(request)

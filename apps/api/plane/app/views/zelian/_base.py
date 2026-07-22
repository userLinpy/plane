# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Auth server-to-server partagée par les endpoints Zelian (provisioning + traces).
# Convention Insider §9.11 / §8.7, règle 07 — secret de service, jamais le JWT identité
# ni un token utilisateur.

import hmac
import os

from rest_framework.permissions import AllowAny

from plane.license.utils.instance_value import get_configuration_value


class ZelianServiceAuthMixin:
    """Habilitation server-to-server par secret de service partagé.

    Le secret est fourni en en-tête ``X-Zelian-Provisioning-Key``, lu via
    ``get_configuration_value`` (config instance chiffrable / fallback env — gestionnaire de
    secrets, règle 07 Insider) et comparé en **temps constant**. **Fail-closed** : sans secret
    configuré, la porte reste fermée pour tout le monde (RM-13).

    Utilisé par ``ZelianProvisioningEndpoint`` (US-01→05) et les endpoints ``traces`` (US-06),
    tous deux appelés par le service de synchronisation Zelian (Manage), pas par un humain.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    # Endpoints machine (server-to-server) : la garde est le secret de service, pas l'IP.
    # On exempte du throttle anonyme DRF (`anon: 30/minute`, common.py) qui, sur ces
    # ``AllowAny``, brimerait à tort les lots légitimes de Manage (offboarding en masse) —
    # un secret erroné est déjà rejeté (403) avant tout traitement.
    throttle_classes = []

    def _is_authorized(self, request):
        (secret,) = get_configuration_value(
            [
                {
                    "key": "ZELIAN_PROVISIONING_SECRET",
                    "default": os.environ.get("ZELIAN_PROVISIONING_SECRET"),
                }
            ]
        )
        # Fail-closed : sans secret configuré, la porte reste fermée pour tout le monde.
        if not secret:
            return False
        provided = request.headers.get("X-Zelian-Provisioning-Key", "")
        return hmac.compare_digest(
            provided.encode("utf-8"), str(secret).encode("utf-8")
        )

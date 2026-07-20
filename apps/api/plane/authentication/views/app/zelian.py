# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import base64
import hashlib
import os
import secrets
import uuid
from urllib.parse import urlencode, urljoin

# Django import
from django.contrib.auth import logout
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.views import View

# Module imports
from plane.authentication.provider.oauth.zelian import ZelianOAuthProvider
from plane.authentication.utils.host import user_ip
from plane.authentication.utils.login import user_login
from plane.db.models import User
from plane.license.utils.instance_value import get_configuration_value
from plane.authentication.utils.redirection_path import get_redirection_path
from plane.authentication.utils.user_auth_workflow import post_user_auth_workflow
from plane.license.models import Instance
from plane.authentication.utils.host import base_host
from plane.authentication.adapter.error import (
    AuthenticationException,
    AUTHENTICATION_ERROR_CODES,
)
from plane.utils.path_validator import validate_next_path


def generate_pkce_pair():
    """Return a (code_verifier, code_challenge) pair for PKCE S256."""
    code_verifier = secrets.token_urlsafe(64)  # 43–128 chars
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().rstrip("=")
    )
    return code_verifier, code_challenge


class ZelianOauthInitiateEndpoint(View):
    def get(self, request):
        # Get host and next path
        request.session["host"] = base_host(request=request, is_app=True)
        next_path = request.GET.get("next_path")
        if next_path:
            request.session["next_path"] = str(validate_next_path(next_path))

        # Check instance configuration
        instance = Instance.objects.first()
        if instance is None or not instance.is_setup_done:
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["INSTANCE_NOT_CONFIGURED"],
                error_message="INSTANCE_NOT_CONFIGURED",
            )
            params = exc.get_error_dict()
            if next_path:
                params["next_path"] = str(validate_next_path(next_path))
            url = urljoin(base_host(request=request, is_app=True), "?" + urlencode(params))
            return HttpResponseRedirect(url)
        try:
            state = uuid.uuid4().hex
            code_verifier, code_challenge = generate_pkce_pair()
            provider = ZelianOAuthProvider(request=request, state=state, code_challenge=code_challenge)
            request.session["state"] = state
            request.session["code_verifier"] = code_verifier
            auth_url = provider.get_auth_url()
            return HttpResponseRedirect(auth_url)
        except AuthenticationException as e:
            params = e.get_error_dict()
            if next_path:
                params["next_path"] = str(validate_next_path(next_path))
            url = urljoin(base_host(request=request, is_app=True), "?" + urlencode(params))
            return HttpResponseRedirect(url)


class ZelianCallbackEndpoint(View):
    def get(self, request):
        code = request.GET.get("code")
        state = request.GET.get("state")
        base_host = request.session.get("host")
        next_path = request.session.get("next_path")

        if state != request.session.get("state", ""):
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ZELIAN_OAUTH_PROVIDER_ERROR"],
                error_message="ZELIAN_OAUTH_PROVIDER_ERROR",
            )
            params = exc.get_error_dict()
            if next_path:
                params["next_path"] = str(next_path)
            url = urljoin(base_host, "?" + urlencode(params))
            return HttpResponseRedirect(url)

        if not code:
            exc = AuthenticationException(
                error_code=AUTHENTICATION_ERROR_CODES["ZELIAN_OAUTH_PROVIDER_ERROR"],
                error_message="ZELIAN_OAUTH_PROVIDER_ERROR",
            )
            params = exc.get_error_dict()
            if next_path:
                params["next_path"] = str(validate_next_path(next_path))
            url = urljoin(base_host, "?" + urlencode(params))
            return HttpResponseRedirect(url)

        try:
            provider = ZelianOAuthProvider(
                request=request,
                code=code,
                code_verifier=request.session.get("code_verifier"),
                callback=post_user_auth_workflow,
            )
            user = provider.authenticate()
            # Login the user and record his device info
            user_login(request=request, user=user, is_app=True)
            # Get the redirection path
            if next_path:
                path = str(validate_next_path(next_path))
            else:
                path = get_redirection_path(user=user)
            # redirect to referer path
            url = urljoin(base_host, path)
            return HttpResponseRedirect(url)
        except AuthenticationException as e:
            params = e.get_error_dict()
            if next_path:
                params["next_path"] = str(validate_next_path(next_path))
            url = urljoin(base_host, "?" + urlencode(params))
            return HttpResponseRedirect(url)


def zelian_post_logout_url(request):
    """Où envoyer l'utilisateur après avoir fermé sa session Plane.

    Sous SSO Zelian, rester dans Plane enferme l'utilisateur : la page de
    connexion relance aussitôt l'auto-login (`auth-root.tsx`), la session
    Supabase est toujours valide, et le voilà reconnecté sans avoir pu sortir.
    On le renvoie donc à la mire, seule habilitée à fermer la session de
    l'écosystème.

    Retourne la racine de Plane si le SSO est inactif ou la destination non
    configurée — le comportement d'origine est alors préservé à l'identique.
    """
    (IS_ZELIAN_ENABLED, ZELIAN_POST_LOGOUT_REDIRECT_URL) = get_configuration_value(
        [
            {
                "key": "IS_ZELIAN_ENABLED",
                "default": os.environ.get("IS_ZELIAN_ENABLED", "0"),
            },
            {
                "key": "ZELIAN_POST_LOGOUT_REDIRECT_URL",
                "default": os.environ.get("ZELIAN_POST_LOGOUT_REDIRECT_URL"),
            },
        ]
    )
    if IS_ZELIAN_ENABLED == "1" and ZELIAN_POST_LOGOUT_REDIRECT_URL:
        return ZELIAN_POST_LOGOUT_REDIRECT_URL
    return base_host(request=request, is_app=True)


class ZelianLogoutEndpoint(View):
    """Déconnexion déclenchable depuis l'extérieur (front-channel logout OIDC).

    Se déconnecter d'une app Zelian doit fermer la session partout, Plane
    compris. Or Plane tient sa propre session Django, indépendante de celle de
    Supabase : détruire l'une laisse l'autre intacte.

    `/auth/sign-out/` ne convient pas ici — il n'accepte que POST, donc une
    redirection depuis la mire ne le déclenche pas. Ce point d'entrée en GET
    joue le rôle du `frontchannel_logout_uri` d'OIDC : la mire y envoie
    l'utilisateur après avoir fermé sa propre session.

    Sécurité — la destination est prise dans la configuration serveur
    (`ZELIAN_POST_LOGOUT_REDIRECT_URL`), jamais dans la requête : un paramètre
    de retour librement fourni ferait de cet endpoint une redirection ouverte.
    À défaut de configuration, on retombe sur la racine de Plane.

    Un endpoint de déconnexion en GET reste exposé au « logout CSRF » — un tiers
    peut forcer la fermeture de session via une simple balise image. La
    conséquence se limite à une déconnexion subie, sans accès ni perte de
    données ; c'est le compromis retenu par le front-channel logout OIDC.
    """

    def get(self, request):
        redirect_url = zelian_post_logout_url(request)

        # Session déjà fermée (ou jamais ouverte) : l'appel reste idempotent,
        # la mire peut nous appeler sans connaître l'état côté Plane.
        if not request.user.is_authenticated:
            return HttpResponseRedirect(redirect_url)

        try:
            user = User.objects.get(pk=request.user.id)
            user.last_logout_ip = user_ip(request=request)
            user.last_logout_time = timezone.now()
            user.save()
        except User.DoesNotExist:
            pass

        logout(request)
        return HttpResponseRedirect(redirect_url)

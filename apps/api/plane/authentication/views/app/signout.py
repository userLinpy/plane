# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.views import View
from django.contrib.auth import logout
from django.http import HttpResponseRedirect
from django.utils import timezone

# Module imports
from plane.authentication.utils.host import user_ip
from plane.authentication.views.app.zelian import zelian_post_logout_url
from plane.db.models import User


class SignOutAuthEndpoint(View):
    def post(self, request):
        # Sous SSO Zelian, la destination est la mire — sinon la page de
        # connexion de Plane relance l'auto-login et reconnecte aussitôt
        # l'utilisateur, qui ne peut alors plus se déconnecter. Hors SSO, le
        # helper retourne la racine de Plane : comportement inchangé.
        redirect_url = zelian_post_logout_url(request)
        # Get user
        try:
            user = User.objects.get(pk=request.user.id)
            user.last_logout_ip = user_ip(request=request)
            user.last_logout_time = timezone.now()
            user.save()
            # Log the user out
            logout(request)
            return HttpResponseRedirect(redirect_url)
        except Exception:
            return HttpResponseRedirect(redirect_url)

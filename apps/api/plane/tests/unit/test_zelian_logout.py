# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Offline unit tests for the Zelian logout path.

Signing out of one Zelian app must close the session everywhere, Plane
included. Plane keeps its own Django session, independent from the Supabase
one, so two things are needed:

* `/auth/zelian/logout/` — a GET entry point the shared sign-in page can call
  (the OIDC `frontchannel_logout_uri` role); `/auth/sign-out/` only accepts
  POST and cannot be triggered by a redirect.
* `zelian_post_logout_url()` — sends the user back to the sign-in page instead
  of Plane's home. Staying inside Plane would trap them: the sign-in page
  re-triggers auto-login, the Supabase session is still valid, and they are
  signed straight back in.

The critical property covered here: the redirect target comes from server
configuration only. Honouring a caller-supplied return URL would turn the
endpoint into an open redirect.
"""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from plane.authentication.views.app.zelian import (
    ZelianLogoutEndpoint,
    zelian_post_logout_url,
)

pytestmark = pytest.mark.django_db

MIRE = "http://localhost:3102/login"


def _request(query=""):
    request = RequestFactory().get(f"/auth/zelian/logout/{query}")
    request.user = AnonymousUser()
    request.session = {}
    return request


def _patch_config(enabled="1", url=MIRE):
    """`zelian_post_logout_url` reads IS_ZELIAN_ENABLED then the target URL."""
    return patch(
        "plane.authentication.views.app.zelian.get_configuration_value",
        return_value=(enabled, url),
    )


def _response(query="", enabled="1", url=MIRE):
    with _patch_config(enabled, url):
        return ZelianLogoutEndpoint().get(_request(query))


class TestPostLogoutUrl:
    def test_uses_configured_url_under_sso(self):
        with _patch_config():
            assert zelian_post_logout_url(_request()) == MIRE

    def test_falls_back_to_plane_home_when_sso_disabled(self):
        """Without SSO, the original behaviour must be preserved exactly."""
        with _patch_config(enabled="0"):
            assert zelian_post_logout_url(_request()) != MIRE

    def test_falls_back_to_plane_home_when_target_unconfigured(self):
        with _patch_config(url=None):
            assert zelian_post_logout_url(_request()) != MIRE


class TestZelianLogoutRedirect:
    def test_redirects_to_configured_url(self):
        response = _response()
        assert response.status_code == 302
        assert response.url == MIRE

    def test_ignores_caller_supplied_return_url(self):
        """An attacker-controlled `next` must never reach the Location header."""
        response = _response("?next=https://attacker.example/steal")
        assert response.url == MIRE
        assert "attacker.example" not in response.url

    @pytest.mark.parametrize(
        "param",
        [
            "?redirect_uri=https://attacker.example",
            "?post_logout_redirect_uri=https://attacker.example",
            "?next=//attacker.example",
        ],
    )
    def test_ignores_every_redirect_parameter_shape(self, param):
        assert _response(param).url == MIRE

    def test_stays_in_plane_when_unconfigured(self):
        """Without configuration, stay inside Plane rather than guess a target."""
        response = _response(url=None)
        assert response.status_code == 302
        assert "attacker" not in response.url
        assert response.url.startswith("http")


class TestZelianLogoutIdempotence:
    def test_anonymous_user_is_not_an_error(self):
        """The sign-in page calls us without knowing whether a session exists."""
        assert _response().status_code == 302

# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views.zelian import ZelianProvisioningEndpoint

urlpatterns = [
    path(
        "zelian/provisioning/",
        ZelianProvisioningEndpoint.as_view(http_method_names=["post"]),
        name="zelian-provisioning",
    ),
]

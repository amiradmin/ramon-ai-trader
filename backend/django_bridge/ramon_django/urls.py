from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", lambda request: JsonResponse({"ok": True, "service": "ramon-django"})),
    path("api/ea/v1/", include("ea_bridge.urls")),
]

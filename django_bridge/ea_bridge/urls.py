from django.urls import path
from . import views

urlpatterns = [
    path("handshake", views.handshake, name="ea-handshake"),
    path("heartbeat", views.heartbeat, name="ea-heartbeat"),
    path("events", views.events, name="ea-events"),
    path("status", views.status, name="ea-status"),
]

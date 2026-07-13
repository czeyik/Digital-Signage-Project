from django.urls import path

from . import api

urlpatterns = [
    path(
        "devices/enrollment-challenge/",
        api.enrollment_challenge,
        name="device-enrollment-challenge",
    ),
    path("devices/enroll/", api.enroll, name="device-enroll"),
    path("devices/token/", api.token_refresh, name="device-token"),
    path("devices/sync/", api.sync_manifest, name="device-sync"),
    path("devices/heartbeat/", api.heartbeat, name="device-heartbeat"),
    path("devices/playback-batches/", api.playback_batch, name="playback-batch"),
    path(
        "devices/operational-events/",
        api.operational_event,
        name="device-operational-event",
    ),
]

from django.urls import path

from . import api

urlpatterns = [
    path("devices/enroll/", api.enroll, name="device-enroll"),
    path("devices/token/", api.token_refresh, name="device-token"),
    path("devices/sync/", api.sync_manifest, name="device-sync"),
    path("devices/heartbeat/", api.heartbeat, name="device-heartbeat"),
    path("devices/playback-batches/", api.playback_batch, name="playback-batch"),
]

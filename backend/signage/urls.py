from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("media/", views.media_list, name="media-list"),
    path("media/upload/", views.media_upload, name="media-upload"),
    path("playlists/", views.playlist_list, name="playlist-list"),
    path("playlists/new/", views.playlist_create, name="playlist-create"),
    path(
        "playlists/<uuid:playlist_id>/",
        views.playlist_detail,
        name="playlist-detail",
    ),
    path(
        "playlists/<uuid:playlist_id>/publish/",
        views.playlist_publish,
        name="playlist-publish",
    ),
    path("devices/", views.device_list, name="device-list"),
    path(
        "devices/<uuid:device_id>/enrollment/",
        views.issue_enrollment,
        name="issue-enrollment",
    ),
    path(
        "devices/<uuid:device_id>/disable/",
        views.device_disable,
        name="device-disable",
    ),
    path(
        "devices/<uuid:device_id>/reactivate/",
        views.device_reactivate,
        name="device-reactivate",
    ),
    path("devices/enrollment-code/", views.enrollment_code, name="enrollment-code"),
    path(
        "alerts/<int:alert_id>/acknowledge/",
        views.acknowledge_alert,
        name="acknowledge-alert",
    ),
    path("reports/playback.csv", views.playback_report_csv, name="playback-csv"),
]

from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("media/", views.media_list, name="media-list"),
    path("media/upload/", views.media_upload, name="media-upload"),
    path("media/<uuid:media_id>/delete/", views.media_delete, name="media-delete"),
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
    path(
        "playlists/<uuid:playlist_id>/clone/",
        views.playlist_clone,
        name="playlist-clone",
    ),
    path("devices/", views.device_list, name="device-list"),
    path("devices/new/", views.device_create, name="device-create"),
    path(
        "devices/<uuid:device_id>/reassign/",
        views.device_reassign,
        name="device-reassign",
    ),
    path(
        "devices/<uuid:device_id>/enrollment/",
        views.issue_enrollment,
        name="issue-enrollment",
    ),
    path(
        "devices/<uuid:device_id>/pin-reset/",
        views.device_pin_reset,
        name="device-pin-reset",
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
    path("devices/kiosk-pin/", views.kiosk_pin, name="kiosk-pin"),
    path("alerts/", views.alert_list, name="alert-list"),
    path(
        "alerts/<int:alert_id>/acknowledge/",
        views.acknowledge_alert,
        name="acknowledge-alert",
    ),
    path("settings/", views.settings_edit, name="settings-edit"),
    path("users/", views.user_list, name="user-list"),
    path("users/new/", views.user_edit, name="user-create"),
    path("users/<int:user_id>/", views.user_edit, name="user-edit"),
    path("reports/playback.csv", views.playback_report_csv, name="playback-csv"),
]

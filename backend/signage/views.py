import csv
import hashlib
import logging
import sqlite3
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (
    INTERNAL_RESET_SESSION_TOKEN,
    LoginView,
    LogoutView,
    PasswordChangeView,
    PasswordResetConfirmView,
    PasswordResetView,
)
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.db.models.functions import TruncDate
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.debug import sensitive_variables
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    DashboardUserForm,
    DeviceProvisioningForm,
    DeviceReassignmentForm,
    MediaUploadForm,
    PlatformSettingsForm,
    PlaylistForm,
)
from .media_dispatch import queue_media_processing
from .models import (
    Alert,
    AuditEvent,
    Device,
    DeviceCommand,
    DeviceLocationPoint,
    EnrollmentCode,
    LoginThrottle,
    MediaAsset,
    PlatformSettings,
    PlaybackCorrection,
    PlaybackEvent,
    Playlist,
    PlaylistItem,
)
from .services import (
    active_playlist,
    audit,
    client_ip,
    delete_media_binary,
    disable_device,
    issue_kiosk_pin,
    open_alert,
    publish_playlist,
    reactivate_device,
    revoke_device_credentials,
    throttle_wait,
)

logger = logging.getLogger(__name__)


def owner_required(user):
    if not user.is_owner:
        raise PermissionDenied


def require_owner(request):
    owner_required(request.user)


def render_one_time_secret(request, template_name, context):
    """Render a newly generated secret without placing it in server-side state."""
    response = render(request, template_name, context)
    response["Cache-Control"] = "no-store, private, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


class SecureLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def _key(self):
        email = self.request.POST.get("username", "").strip().lower()
        ip = client_ip(self.request)
        value = f"login-failures|{email}|{ip}|{settings.SECRET_KEY}"
        return hashlib.sha256(value.encode()).hexdigest()

    def post(self, request, *args, **kwargs):
        throttle = LoginThrottle.objects.filter(key_hash=self._key()).first()
        if throttle and throttle.is_locked:
            messages.error(
                request,
                "Too many sign-in attempts. Try again in 15 minutes.",
            )
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self._key()
        with transaction.atomic():
            throttle, _ = LoginThrottle.objects.select_for_update().get_or_create(
                key_hash=key
            )
            throttle.failures += 1
            if throttle.failures >= 5:
                throttle.locked_until = timezone.now() + timedelta(minutes=15)
            throttle.save()
        AuditEvent.objects.create(
            action="auth.login_failed",
            target_type="user",
            target_id=hashlib.sha256(
                self.request.POST.get("username", "").lower().encode()
            ).hexdigest(),
            metadata={"attempt": throttle.failures},
        )
        if throttle.failures >= 5:
            open_alert(
                None,
                "suspicious_login_lockout",
                Alert.Severity.WARNING,
                "Repeated failed dashboard sign-in attempts triggered a lockout.",
            )
        # Replace Django's field-specific message with a generic response.
        form.errors.clear()
        form.add_error(None, "Invalid email or password.")
        return super().form_invalid(form)

    def form_valid(self, form):
        # Persist the required audit event before mutating the in-memory
        # session. If audit storage is unavailable, no authenticated session
        # may escape through SessionMiddleware after the failed request.
        with transaction.atomic():
            user = form.get_user()
            LoginThrottle.objects.filter(key_hash=self._key()).delete()
            audit(user, "auth.login", user)
            response = super().form_valid(form)
        return response


class SecureAdminLoginView(SecureLoginView):
    """Apply dashboard throttling/auditing while admitting account owners only."""

    def form_valid(self, form):
        if not form.get_user().is_owner:
            return self.form_invalid(form)
        return super().form_valid(form)


class AuditedLogoutView(LogoutView):
    def post(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            audit(request.user, "auth.logout", request.user)
        return super().post(request, *args, **kwargs)


class AuditedPasswordChangeView(PasswordChangeView):
    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            audit(self.request.user, "auth.password_change", self.request.user)
        return response


class InvalidPasswordResetToken(Exception):
    """The reset token was consumed or expired before the password write."""


@sensitive_variables("token", "raw_password")
@transaction.atomic
def _complete_password_reset(user_id, token, raw_password, token_generator):
    """Consume one reset token while holding the user row lock."""
    user_model = get_user_model()
    user = user_model.objects.select_for_update().get(pk=user_id)
    if not token_generator.check_token(user, token):
        raise InvalidPasswordResetToken
    user.set_password(raw_password)
    user.save(update_fields=["password"])
    audit(None, "auth.password_reset", user)
    return user


class SecurePasswordResetConfirmView(PasswordResetConfirmView):
    """Make reset-token consumption single-use and transactionally audited."""

    def form_valid(self, form):
        session_token = self.request.session.get(INTERNAL_RESET_SESSION_TOKEN)
        try:
            user = _complete_password_reset(
                self.user.pk,
                session_token,
                form.cleaned_data["new_password1"],
                self.token_generator,
            )
        except (InvalidPasswordResetToken, get_user_model().DoesNotExist):
            self.request.session.pop(INTERNAL_RESET_SESSION_TOKEN, None)
            self.validlink = False
            return self.render_to_response(self.get_context_data())

        self.user = user
        self.request.session.pop(INTERNAL_RESET_SESSION_TOKEN, None)
        if self.post_reset_login:
            auth_login(self.request, user, self.post_reset_login_backend)
        return redirect(self.get_success_url())


class SecurePasswordResetView(PasswordResetView):
    def post(self, request, *args, **kwargs):
        wait = throttle_wait(request, "password_reset", limit=5, window_seconds=900)
        email = request.POST.get("email", "").strip().lower()
        AuditEvent.objects.create(
            action="auth.password_reset_requested",
            target_type="user",
            target_id=hashlib.sha256(email.encode()).hexdigest(),
        )
        if wait:
            messages.error(
                request,
                "If that account exists, reset instructions will be sent shortly.",
            )
            return self.get(request, *args, **kwargs)
        return super().post(request, *args, **kwargs)


def health_live(request):
    return JsonResponse({"status": "ok"})


def health_ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})


@login_required
def dashboard(request):
    now = timezone.now()
    today = timezone.localdate()
    offline_before = now - timedelta(minutes=60)
    devices = Device.objects.all()
    chart_start = today - timedelta(days=6)
    chart_start_at = timezone.make_aware(datetime.combine(chart_start, time.min))
    tomorrow_at = timezone.make_aware(
        datetime.combine(today + timedelta(days=1), time.min)
    )
    chart_rows = (
        PlaybackEvent.objects.filter(started_at__gte=chart_start_at)
        .annotate(day=TruncDate("started_at"))
        .values("day", "status")
        .annotate(total=Count("id"))
    )
    chart_counts = {(row["day"], row["status"]): row["total"] for row in chart_rows}
    playback_chart = []
    for offset in range(7):
        day = chart_start + timedelta(days=offset)
        playback_chart.append(
            {
                "day": day,
                "completed": chart_counts.get((day, PlaybackEvent.Status.COMPLETED), 0),
                "interrupted": chart_counts.get(
                    (day, PlaybackEvent.Status.INTERRUPTED), 0
                ),
                "failed": chart_counts.get((day, PlaybackEvent.Status.FAILED), 0),
            }
        )
    chart_max = max(
        (
            row["completed"] + row["interrupted"] + row["failed"]
            for row in playback_chart
        ),
        default=1,
    )
    current_playlist = active_playlist()
    today_events = PlaybackEvent.objects.filter(
        started_at__gte=timezone.make_aware(datetime.combine(today, time.min)),
        started_at__lt=tomorrow_at,
    )
    context = {
        "device_count": devices.count(),
        "active_count": devices.filter(status=Device.Status.ACTIVE).count(),
        "offline_count": devices.filter(
            Q(last_seen_at__lt=offline_before) | Q(last_seen_at__isnull=True)
        ).count(),
        "unresolved_alerts": Alert.objects.filter(
            acknowledged_at__isnull=True
        ).select_related("device")[:10],
        "unresolved_alert_count": Alert.objects.filter(
            acknowledged_at__isnull=True
        ).count(),
        "ready_media_count": MediaAsset.objects.filter(
            status=MediaAsset.Status.READY
        ).count(),
        "today_completed_count": today_events.filter(
            status=PlaybackEvent.Status.COMPLETED
        ).count(),
        "today_interrupted_count": today_events.filter(
            status=PlaybackEvent.Status.INTERRUPTED
        ).count(),
        "today_failed_count": today_events.filter(
            status=PlaybackEvent.Status.FAILED
        ).count(),
        "active_playlist": current_playlist,
        "published_playlist": Playlist.objects.filter(status=Playlist.Status.PUBLISHED)
        .order_by("-published_at")
        .first(),
        "devices": devices.order_by("label")[:20],
        "playback_chart": playback_chart,
        "chart_max": max(chart_max, 1),
    }
    return render(request, "signage/dashboard.html", context)


@login_required
def location_map(request):
    return render(
        request,
        "signage/location_map.html",
        {
            "location_map_style_url": settings.OPENMAPTILES_STYLE_URL,
        },
    )


def _openmaptiles_style(request):
    return {
        "version": 8,
        "name": "DUDU OpenMapTiles",
        "center": [101.6869, 3.139],
        "zoom": 8,
        "sources": {
            "openmaptiles": {
                "type": "vector",
                "url": request.build_absolute_uri("/locations/tiles.json"),
                "attribution": "© OpenStreetMap contributors · © OpenMapTiles",
            }
        },
        "layers": [
            {
                "id": "background",
                "type": "background",
                "paint": {"background-color": "#eef2f5"},
            },
            {
                "id": "landcover",
                "type": "fill",
                "source": "openmaptiles",
                "source-layer": "landcover",
                "paint": {"fill-color": "#d9ead3", "fill-opacity": 0.7},
            },
            {
                "id": "landuse",
                "type": "fill",
                "source": "openmaptiles",
                "source-layer": "landuse",
                "paint": {"fill-color": "#e8f0df", "fill-opacity": 0.8},
            },
            {
                "id": "water",
                "type": "fill",
                "source": "openmaptiles",
                "source-layer": "water",
                "paint": {"fill-color": "#b9d9ee"},
            },
            {
                "id": "waterway",
                "type": "line",
                "source": "openmaptiles",
                "source-layer": "waterway",
                "paint": {
                    "line-color": "#8fc5e8",
                    "line-width": [
                        "interpolate",
                        ["linear"],
                        ["zoom"],
                        5,
                        0.5,
                        13,
                        2,
                    ],
                },
            },
            {
                "id": "building",
                "type": "fill",
                "source": "openmaptiles",
                "source-layer": "building",
                "minzoom": 13,
                "paint": {
                    "fill-color": "#d8d5d0",
                    "fill-outline-color": "#c1bdb6",
                    "fill-opacity": 0.75,
                },
            },
            {
                "id": "transportation",
                "type": "line",
                "source": "openmaptiles",
                "source-layer": "transportation",
                "paint": {
                    "line-color": "#ffffff",
                    "line-width": [
                        "interpolate",
                        ["linear"],
                        ["zoom"],
                        5,
                        0.5,
                        12,
                        1.5,
                        16,
                        6,
                    ],
                },
            },
            {
                "id": "transportation-major",
                "type": "line",
                "source": "openmaptiles",
                "source-layer": "transportation",
                "filter": [
                    "match",
                    ["get", "class"],
                    ["motorway", "trunk", "primary", "secondary"],
                    True,
                    False,
                ],
                "paint": {
                    "line-color": "#f0a35b",
                    "line-width": [
                        "interpolate",
                        ["linear"],
                        ["zoom"],
                        5,
                        1,
                        12,
                        2.5,
                        16,
                        8,
                    ],
                },
            },
        ],
    }


@login_required
def location_style(request):
    response = JsonResponse(_openmaptiles_style(request))
    response["Cache-Control"] = "private, max-age=3600"
    return response


@login_required
def location_tilejson(request):
    tile_url = (
        request.build_absolute_uri("/").rstrip("/")
        + "/locations/tiles/{z}/{x}/{y}.pbf"
    )
    response = JsonResponse(
        {
            "tilejson": "3.0.0",
            "name": "DUDU OpenMapTiles",
            "scheme": "xyz",
            "format": "pbf",
            "minzoom": 0,
            "maxzoom": settings.OPENMAPTILES_MAX_ZOOM,
            "bounds": [99.0, 0.0, 120.0, 8.0],
            "tiles": [tile_url],
            "vector_layers": [
                {"id": layer}
                for layer in (
                    "landcover",
                    "landuse",
                    "water",
                    "waterway",
                    "building",
                    "transportation",
                )
            ],
            "attribution": "© OpenStreetMap contributors · © OpenMapTiles",
        }
    )
    response["Cache-Control"] = "private, max-age=3600"
    return response


def _openmaptiles_path():
    path = Path(settings.OPENMAPTILES_MBTILES_PATH)
    if path.is_symlink() or not path.is_file():
        return None
    return path


@login_required
def location_tile(request, z, x, y):
    if (
        z < 0
        or x < 0
        or y < 0
        or z > settings.OPENMAPTILES_MAX_ZOOM
        or x >= 2**z
        or y >= 2**z
    ):
        return HttpResponse("Map tile is outside the configured bounds.", status=404)
    path = _openmaptiles_path()
    if path is None:
        return HttpResponse("Map tiles are not installed.", status=503)

    # MBTiles stores rows in TMS order while MapLibre requests XYZ rows.
    tile_row = (2**z) - 1 - y
    try:
        database_uri = f"{path.as_uri()}?mode=ro"
        with sqlite3.connect(database_uri, uri=True, timeout=1) as database:
            row = database.execute(
                """
                SELECT tile_data
                FROM tiles
                WHERE zoom_level = ? AND tile_column = ? AND tile_row = ?
                """,
                (z, x, tile_row),
            ).fetchone()
    except sqlite3.Error:
        logger.warning("OpenMapTiles database read failed", exc_info=True)
        return HttpResponse("Map tiles are temporarily unavailable.", status=503)

    if row is None:
        return HttpResponse("Map tile not found.", status=404)
    tile_data = bytes(row[0])
    response = HttpResponse(
        tile_data, content_type="application/vnd.mapbox-vector-tile"
    )
    if tile_data.startswith(b"\x1f\x8b"):
        response["Content-Encoding"] = "gzip"
    response["Cache-Control"] = "private, max-age=3600"
    return response


def _location_point_json(point):
    assignment = point.assignment
    return {
        "id": str(point.id),
        "device_id": str(point.device_id),
        "device_label": point.device.label,
        "vehicle_registration": assignment.vehicle.registration if assignment else None,
        "driver_internal_id": assignment.driver.internal_id if assignment else None,
        "latitude": float(point.latitude),
        "longitude": float(point.longitude),
        "accuracy_m": float(point.accuracy_m),
        "provider": point.provider,
        "recorded_at": point.recorded_at,
        "received_at": point.received_at,
    }


@login_required
def location_latest(request):
    rows = []
    for device in Device.objects.filter(status=Device.Status.ACTIVE).order_by("label"):
        point = (
            DeviceLocationPoint.objects.filter(device=device)
            .select_related("device", "assignment__vehicle", "assignment__driver")
            .order_by("-recorded_at")
            .first()
        )
        rows.append(
            {
                "device_id": str(device.id),
                "device_label": device.label,
                "state": device.location_state,
                "state_updated_at": device.location_state_updated_at,
                "last_reported_at": device.last_location_reported_at,
                "planned_gap_until": device.location_planned_gap_until,
                "point": _location_point_json(point) if point else None,
            }
        )
    return JsonResponse({"devices": rows})


@login_required
def location_history(request):
    raw_device_id = request.GET.get("device_id", "")
    try:
        device_id = uuid.UUID(raw_device_id)
    except (TypeError, ValueError):
        return JsonResponse({"error": "device_id is required."}, status=400)
    device = get_object_or_404(Device, pk=device_id)
    now = timezone.now()
    today = timezone.localdate()
    default_start = timezone.make_aware(datetime.combine(today, time.min))
    start = _parse_location_query_datetime(request.GET.get("start"), default_start)
    end = _parse_location_query_datetime(request.GET.get("end"), now)
    if start is None or end is None or end <= start:
        return JsonResponse({"error": "Invalid history time range."}, status=400)
    if start < now - timedelta(days=30) or end > now + timedelta(minutes=5):
        return JsonResponse(
            {"error": "History must be within the preceding 30 days."},
            status=400,
        )
    if end - start > timedelta(hours=24):
        return JsonResponse({"error": "History is limited to 24 hours."}, status=400)
    points = (
        DeviceLocationPoint.objects.filter(
            device=device,
            recorded_at__gte=start,
            recorded_at__lt=end,
        )
        .select_related("device", "assignment__vehicle", "assignment__driver")
        .order_by("recorded_at")
    )
    return JsonResponse(
        {
            "device": {"id": str(device.id), "label": device.label},
            "start": start,
            "end": end,
            "points": [_location_point_json(point) for point in points],
        }
    )


def _parse_location_query_datetime(value, default):
    if not value:
        return default
    try:
        parsed = parse_datetime(value)
        if parsed and timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
    except (OverflowError, TypeError, ValueError):
        return None
    return parsed


@login_required
def media_list(request):
    return render(
        request,
        "signage/media_list.html",
        {"assets": MediaAsset.objects.order_by("-created_at")},
    )


@login_required
@require_POST
def media_delete(request, media_id):
    asset = get_object_or_404(MediaAsset, pk=media_id)
    if request.POST.get("confirm") != "delete":
        messages.error(request, "Confirm binary deletion before continuing.")
        return redirect("media-list")
    try:
        delete_media_binary(asset, request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(
            request,
            "Media archived; binary deletion is queued for durable reconciliation.",
        )
    return redirect("media-list")


@login_required
def media_upload(request):
    form = MediaUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            asset = form.save(commit=False)
            asset.uploaded_by = request.user
            asset.save()
            audit(request.user, "media.upload", asset)
            queue_media_processing(asset.id)
        messages.success(
            request,
            "Upload quarantined and queued for validation.",
        )
        return redirect("media-list")
    return render(request, "signage/form.html", {"form": form, "title": "Upload media"})


@login_required
def playlist_list(request):
    return render(
        request,
        "signage/playlist_list.html",
        {
            "playlists": Playlist.objects.exclude(
                status=Playlist.Status.CANCELLED
            ).prefetch_related("items")
        },
    )


@login_required
@transaction.atomic
def playlist_detail(request, playlist_id):
    playlist = get_object_or_404(Playlist.objects.select_for_update(), pk=playlist_id)
    if request.method == "POST":
        if playlist.status != Playlist.Status.DRAFT:
            raise PermissionDenied("Published playlists are immutable.")
        action = request.POST.get("action")
        if action == "add":
            media = get_object_or_404(
                MediaAsset,
                pk=request.POST.get("media_id"),
                status=MediaAsset.Status.READY,
            )
            position = playlist.items.count() + 1
            item = PlaylistItem.objects.create(
                playlist=playlist, media=media, position=position
            )
            audit(request.user, "playlist.item.add", playlist, {"item": str(item.id)})
        elif action == "remove":
            item = get_object_or_404(
                PlaylistItem, pk=request.POST.get("item_id"), playlist=playlist
            )
            removed_id = str(item.id)
            item.delete()
            for position, remaining in enumerate(playlist.items.all(), start=1):
                if remaining.position != position:
                    remaining.position = position
                    remaining.save(update_fields=["position"])
            audit(
                request.user,
                "playlist.item.remove",
                playlist,
                {"item": removed_id},
            )
        elif action == "reorder":
            ordered_ids = [
                value for value in request.POST.get("order", "").split(",") if value
            ]
            current_ids = [
                str(value) for value in playlist.items.values_list("id", flat=True)
            ]
            if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(
                current_ids
            ):
                raise ValidationError("Order must contain every playlist item once.")
            playlist.items.update(position=F("position") + 10_000)
            for position, item_id in enumerate(ordered_ids, start=1):
                PlaylistItem.objects.filter(pk=item_id, playlist=playlist).update(
                    position=position
                )
            audit(request.user, "playlist.reorder", playlist)
        return redirect("playlist-detail", playlist_id=playlist.id)
    return render(
        request,
        "signage/playlist_detail.html",
        {
            "playlist": playlist,
            "ready_media": MediaAsset.objects.filter(
                status=MediaAsset.Status.READY
            ).order_by("business_name", "title"),
            "superseded_versions": Playlist.objects.filter(
                name=playlist.name,
                starts_at=playlist.starts_at,
                ends_at=playlist.ends_at,
                version__lt=playlist.version,
            )
            .exclude(status=Playlist.Status.DRAFT)
            .prefetch_related("items")
            .order_by("-version"),
        },
    )


@login_required
@transaction.atomic
def playlist_create(request):
    form = PlaylistForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        playlist = form.save(commit=False)
        playlist.created_by = request.user
        playlist.save()
        for position, media in enumerate(form.cleaned_data["media"], start=1):
            PlaylistItem.objects.create(
                playlist=playlist, media=media, position=position
            )
        audit(request.user, "playlist.create", playlist)
        messages.success(request, "Draft playlist created.")
        return redirect("playlist-list")
    return render(
        request, "signage/form.html", {"form": form, "title": "Create playlist"}
    )


@login_required
@require_POST
def playlist_publish(request, playlist_id):
    playlist = get_object_or_404(Playlist, pk=playlist_id)
    try:
        publish_playlist(
            playlist, request.user, urgent=request.POST.get("urgent") == "true"
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Playlist published.")
    return redirect("playlist-list")


@login_required
@require_POST
@transaction.atomic
def playlist_clone(request, playlist_id):
    source = get_object_or_404(
        Playlist.objects.prefetch_related("items__media"), pk=playlist_id
    )
    latest = (
        Playlist.objects.filter(name=source.name)
        .order_by("-version")
        .values_list("version", flat=True)
        .first()
    )
    clone = Playlist.objects.create(
        name=source.name,
        version=(latest or source.version) + 1,
        starts_at=source.starts_at,
        ends_at=source.ends_at,
        created_by=request.user,
    )
    PlaylistItem.objects.bulk_create(
        [
            PlaylistItem(playlist=clone, media=item.media, position=item.position)
            for item in source.items.all()
        ]
    )
    audit(request.user, "playlist.clone_version", clone, {"source": str(source.id)})
    messages.success(request, f"Created editable {clone.name} v{clone.version}.")
    return redirect("playlist-detail", playlist_id=clone.id)


@login_required
def device_list(request):
    return render(
        request,
        "signage/device_list.html",
        {"devices": Device.objects.prefetch_related("assignments").order_by("label")},
    )


@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def device_create(request):
    require_owner(request)
    form = DeviceProvisioningForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        device = form.save()
        audit(request.user, "device.provision", device)
        raw_pin = issue_kiosk_pin(device, request.user)
        return render_one_time_secret(
            request,
            "signage/kiosk_pin.html",
            {"device": device.label, "pin": raw_pin},
        )
    return render(
        request,
        "signage/form.html",
        {"form": form, "title": "Add device and assignment"},
    )


@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def device_reassign(request, device_id):
    require_owner(request)
    devices = (
        Device.objects.select_for_update()
        if request.method == "POST"
        else Device.objects
    )
    device = get_object_or_404(devices, pk=device_id)
    form = DeviceReassignmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save(device)
        EnrollmentCode.objects.filter(device=device, used_at__isnull=True).update(
            expires_at=timezone.now()
        )
        audit(request.user, "device.reassign", device)
        messages.success(
            request,
            "Device reassigned. Assignment history was preserved.",
        )
        return redirect("device-list")
    return render(
        request,
        "signage/form.html",
        {"form": form, "title": f"Reassign {device.label}"},
    )


@login_required
@require_POST
@transaction.atomic
def issue_enrollment(request, device_id):
    require_owner(request)
    device = get_object_or_404(Device.objects.select_for_update(), pk=device_id)
    if device.status == Device.Status.DISABLED:
        messages.error(request, "Reactivate the device before enrollment.")
        return redirect("device-list")
    if not device.assignments.filter(unassigned_at__isnull=True).exists():
        messages.error(request, "Assign a car and driver before enrollment.")
        return redirect("device-list")
    if not device.kiosk_pin_hash:
        messages.error(
            request,
            "The account owner must set the kiosk administrator PIN before enrollment.",
        )
        return redirect("device-list")
    _, raw_code = EnrollmentCode.issue(device, request.user)
    audit(request.user, "device.enrollment_code.issue", device)
    return render_one_time_secret(
        request,
        "signage/enrollment_code.html",
        {"device": device.label, "code": raw_code},
    )


@login_required
@require_POST
@transaction.atomic
def device_pin_reset(request, device_id):
    require_owner(request)
    device = get_object_or_404(Device.objects.select_for_update(), pk=device_id)
    EnrollmentCode.objects.filter(device=device, used_at__isnull=True).update(
        expires_at=timezone.now()
    )
    raw_pin = issue_kiosk_pin(device, request.user)
    return render_one_time_secret(
        request,
        "signage/kiosk_pin.html",
        {"device": device.label, "pin": raw_pin},
    )


@login_required
@require_POST
@transaction.atomic
def device_disable(request, device_id):
    require_owner(request)
    device = get_object_or_404(Device.objects.select_for_update(), pk=device_id)
    disable_device(device, request.user)
    messages.success(
        request,
        "Playback disabled and playback credentials revoked. The management "
        "channel remains available for Admin mode.",
    )
    return redirect("device-list")


@login_required
@require_POST
def device_reactivate(request, device_id):
    require_owner(request)
    device = get_object_or_404(Device, pk=device_id)
    reactivate_device(device, request.user)
    messages.success(request, "Device explicitly reactivated.")
    return redirect("device-list")


@login_required
@require_POST
@transaction.atomic
def device_admin_mode(request, device_id):
    require_owner(request)
    device = get_object_or_404(Device.objects.select_for_update(), pk=device_id)
    now = timezone.now()
    existing = DeviceCommand.objects.filter(
        device=device,
        kind=DeviceCommand.Kind.ADMIN_MODE,
        acknowledged_at__isnull=True,
        expires_at__gt=now,
    ).first()
    if existing is None:
        command = DeviceCommand.objects.create(
            device=device,
            kind=DeviceCommand.Kind.ADMIN_MODE,
            requested_by=request.user,
            expires_at=now + timedelta(minutes=10),
        )
        audit(request.user, "device.admin_mode.request", command)
        messages.success(request, "Admin mode requested for the next device check-in.")
    else:
        messages.info(request, "An Admin mode request is already pending.")
    return redirect("device-list")


@login_required
@require_POST
@transaction.atomic
def device_credentials_revoke(request, device_id):
    require_owner(request)
    device = get_object_or_404(Device.objects.select_for_update(), pk=device_id)
    _, credential_count = revoke_device_credentials(device, request.user)
    messages.success(
        request,
        f"Revoked {credential_count} active playback credential(s), revoked the "
        "management credential, and expired any unused enrollment code. "
        "Re-enrollment is required before this player can reconnect.",
    )
    return redirect("device-list")


@login_required
def enrollment_code(request):
    # Legacy releases persisted this raw secret in database-backed sessions.
    # Drop it without redisplaying it; current releases render only in the POST
    # response that creates the code.
    request.session.pop("one_time_enrollment_code", None)
    return redirect("device-list")


@login_required
def kiosk_pin(request):
    require_owner(request)
    request.session.pop("one_time_kiosk_pin", None)
    return redirect("device-list")


@login_required
@require_POST
@transaction.atomic
def acknowledge_alert(request, alert_id):
    alert = get_object_or_404(
        Alert.objects.select_for_update(),
        pk=alert_id,
        acknowledged_at__isnull=True,
    )
    alert.acknowledged_at = timezone.now()
    alert.acknowledged_by = request.user
    alert.save(update_fields=["acknowledged_at", "acknowledged_by", "updated_at"])
    audit(request.user, "alert.acknowledge", alert)
    return redirect("dashboard")


@login_required
def alert_list(request):
    return render(
        request,
        "signage/alert_list.html",
        {
            "alerts": Alert.objects.select_related(
                "device", "acknowledged_by"
            ).order_by("acknowledged_at", "-created_at")[:200]
        },
    )


@login_required
@transaction.atomic
def settings_edit(request):
    require_owner(request)
    settings_object = (
        PlatformSettings.objects.select_for_update().get_or_create(singleton_id=1)[0]
        if request.method == "POST"
        else PlatformSettings.load()
    )
    form = PlatformSettingsForm(request.POST or None, instance=settings_object)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(request.user, "settings.update", settings_object)
        messages.success(request, "Pilot limits updated.")
        return redirect("settings-edit")
    return render(
        request,
        "signage/form.html",
        {"form": form, "title": "Pilot limits"},
    )


@login_required
def user_list(request):
    require_owner(request)
    return render(
        request,
        "signage/user_list.html",
        {"users": get_user_model().objects.order_by("email")},
    )


@login_required
@require_http_methods(["GET", "POST"])
@transaction.atomic
def user_edit(request, user_id=None):
    require_owner(request)
    model = get_user_model()
    active_owner_ids = ()
    if request.method == "POST":
        # Lock the complete invariant set in one stable order before locking the
        # target row. Concurrent demotions cannot each observe the other owner
        # and leave the dashboard without an active owner.
        active_owner_ids = tuple(
            model.objects.select_for_update()
            .filter(role=model.Role.OWNER, is_active=True)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
    users = (
        model.objects.select_for_update()
        if request.method == "POST"
        else model.objects
    )
    user_object = get_object_or_404(users, pk=user_id) if user_id else model()
    was_active_owner = bool(
        user_object.pk and user_object.is_owner and user_object.is_active
    )
    form = DashboardUserForm(request.POST or None, instance=user_object)
    if request.method == "POST" and form.is_valid():
        removing_active_owner = (
            was_active_owner
            and (
                form.cleaned_data["role"] != model.Role.OWNER
                or not form.cleaned_data["is_active"]
            )
        )
        if removing_active_owner:
            if not any(owner_id != user_object.pk for owner_id in active_owner_ids):
                form.add_error(
                    None,
                    "At least one active account owner must remain.",
                )
        if not form.errors:
            saved = form.save()
            audit(
                request.user,
                "user.update" if user_id else "user.create",
                saved,
                {"role": saved.role, "active": saved.is_active},
            )
            messages.success(request, "Dashboard user saved.")
            return redirect("user-list")
    return render(
        request,
        "signage/form.html",
        {"form": form, "title": "Dashboard user"},
    )


@login_required
def playback_report_csv(request):
    class Echo:
        def write(self, value):
            return value

    def csv_cell(value):
        if isinstance(value, str) and value.lstrip(" \t\r\n\v\f\x00").startswith(
            ("=", "+", "-", "@")
        ):
            return f"'{value}"
        return value

    events = (
        PlaybackEvent.objects.select_related(
            "batch__device",
            "batch__assignment__vehicle",
            "batch__assignment__driver",
            "batch__playlist",
            "playlist_item__media",
        )
        .annotate(
            latest_correction_status=Subquery(
                PlaybackCorrection.objects.filter(event_id=OuterRef("pk"))
                .order_by("-created_at")
                .values("replacement_status")[:1]
            )
        )
        .order_by("-started_at")
    )
    filters = {
        "device": "batch__device__label",
        "vehicle": "batch__assignment__vehicle__registration",
        "driver": "batch__assignment__driver__internal_id",
        "media": "playlist_item__media_id",
        "campaign": "playlist_item__media__business_name",
        "status": "status",
    }
    for parameter, lookup in filters.items():
        value = request.GET.get(parameter, "").strip()
        if value:
            events = events.filter(**{lookup: value})
    for parameter, is_start in (("date_from", True), ("date_to", False)):
        raw_date = request.GET.get(parameter, "").strip()
        if raw_date:
            parsed_date = parse_date(raw_date)
            if not parsed_date:
                return HttpResponse(f"Invalid {parameter}; use YYYY-MM-DD.", status=400)
            boundary = timezone.make_aware(
                datetime.combine(
                    parsed_date + (timedelta(days=1) if not is_start else timedelta()),
                    time.min,
                )
            )
            if is_start:
                events = events.filter(started_at__gte=boundary)
            else:
                events = events.filter(started_at__lt=boundary)
    offline = request.GET.get("offline", "").lower()
    if offline in {"true", "false"}:
        events = events.filter(batch__captured_offline=offline == "true")
    events = events[: settings.CSV_EXPORT_MAX_ROWS]
    AuditEvent.objects.create(
        actor=request.user,
        action="report.playback.export",
        target_type="playback_event",
        target_id="csv",
        metadata={
            "filters": {
                parameter: request.GET.get(parameter, "")
                for parameter in (*filters, "date_from", "date_to", "offline")
                if request.GET.get(parameter, "")
            },
            "max_rows": settings.CSV_EXPORT_MAX_ROWS,
        },
    )
    response = StreamingHttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="proof-of-play.csv"'
    evidence_notice = (
        "Commercially useful evidence; not independently audited or tamper-proof."
    )
    exported_at = timezone.now()

    def rows():
        writer = csv.writer(Echo())
        yield writer.writerow(
            [
                "event_id",
                "device",
                "vehicle",
                "driver_internal_id",
                "playlist",
                "media",
                "started_at",
                "status",
                "duration_ms",
                "captured_offline",
                "report_state",
                "latest_correction_status",
                "failure_category",
                "evidence_notice",
            ]
        )
        for event in events.iterator(chunk_size=500):
            assignment = event.batch.assignment
            final_at = max(event.started_at, event.batch.received_at) + timedelta(
                days=7
            )
            yield writer.writerow(
                [
                    *(
                        csv_cell(value)
                        for value in (
                            str(event.id),
                            event.batch.device.label,
                            assignment.vehicle.registration if assignment else "",
                            assignment.driver.internal_id if assignment else "",
                            str(event.batch.playlist),
                            event.playlist_item.media.title,
                            event.started_at.isoformat(),
                            event.status,
                            event.duration_ms,
                            event.batch.captured_offline,
                            "final" if exported_at >= final_at else "provisional",
                            event.latest_correction_status or "",
                            event.failure_reason,
                            evidence_notice,
                        )
                    ),
                ]
            )

    response.streaming_content = rows()
    return response

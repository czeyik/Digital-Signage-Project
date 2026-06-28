import csv
import hashlib
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, F, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    DashboardUserForm,
    DeviceProvisioningForm,
    DeviceReassignmentForm,
    MediaUploadForm,
    PlatformSettingsForm,
    PlaylistForm,
)
from .models import (
    Alert,
    AuditEvent,
    Device,
    EnrollmentCode,
    LoginThrottle,
    MediaAsset,
    PlatformSettings,
    PlaybackEvent,
    Playlist,
    PlaylistItem,
)
from .services import (
    audit,
    delete_media_binary,
    disable_device,
    issue_kiosk_pin,
    open_alert,
    publish_playlist,
    reactivate_device,
)


def owner_required(user):
    if not user.is_owner:
        raise PermissionDenied


def require_owner(request):
    owner_required(request.user)


class SecureLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def _key(self):
        email = self.request.POST.get("username", "").strip().lower()
        forwarded = self.request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip = forwarded.split(",")[0].strip() or self.request.META.get("REMOTE_ADDR", "")
        value = f"{email}|{ip}|{settings.SECRET_KEY}"
        return f"login-failures:{hashlib.sha256(value.encode()).hexdigest()}"

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
        LoginThrottle.objects.filter(key_hash=self._key()).delete()
        response = super().form_valid(form)
        audit(self.request.user, "auth.login", self.request.user)
        return response


@login_required
def dashboard(request):
    now = timezone.now()
    offline_before = now - timedelta(minutes=60)
    devices = Device.objects.all()
    chart_start = timezone.localdate() - timedelta(days=6)
    chart_rows = (
        PlaybackEvent.objects.filter(started_at__date__gte=chart_start)
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
        "published_playlist": Playlist.objects.filter(status=Playlist.Status.PUBLISHED)
        .order_by("-published_at")
        .first(),
        "devices": devices.order_by("label")[:20],
        "playback_chart": playback_chart,
        "chart_max": max(chart_max, 1),
    }
    return render(request, "signage/dashboard.html", context)


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
    try:
        delete_media_binary(asset, request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Media binaries removed and metadata archived.")
    return redirect("media-list")


@login_required
def media_upload(request):
    form = MediaUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        asset = form.save(commit=False)
        asset.uploaded_by = request.user
        asset.save()
        audit(request.user, "media.upload", asset)
        messages.success(
            request,
            "Upload quarantined. Run the media processor before using it.",
        )
        return redirect("media-list")
    return render(request, "signage/form.html", {"form": form, "title": "Upload media"})


@login_required
def playlist_list(request):
    return render(
        request,
        "signage/playlist_list.html",
        {"playlists": Playlist.objects.prefetch_related("items").all()},
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
def device_list(request):
    return render(
        request,
        "signage/device_list.html",
        {"devices": Device.objects.prefetch_related("assignments").order_by("label")},
    )


@login_required
@require_http_methods(["GET", "POST"])
def device_create(request):
    require_owner(request)
    form = DeviceProvisioningForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        device = form.save()
        audit(request.user, "device.provision", device)
        messages.success(request, "Device, driver, and vehicle assignment created.")
        return redirect("device-list")
    return render(
        request,
        "signage/form.html",
        {"form": form, "title": "Add device and assignment"},
    )


@login_required
@require_http_methods(["GET", "POST"])
def device_reassign(request, device_id):
    require_owner(request)
    device = get_object_or_404(Device, pk=device_id)
    form = DeviceReassignmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save(device)
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
def issue_enrollment(request, device_id):
    device = get_object_or_404(Device, pk=device_id)
    if not device.assignments.filter(unassigned_at__isnull=True).exists():
        messages.error(request, "Assign a car and driver before enrollment.")
        return redirect("device-list")
    EnrollmentCode.objects.filter(device=device, used_at__isnull=True).update(
        expires_at=timezone.now()
    )
    _, raw_code = EnrollmentCode.issue(device, request.user)
    audit(request.user, "device.enrollment_code.issue", device)
    request.session["one_time_enrollment_code"] = {
        "device": device.label,
        "code": raw_code,
    }
    return redirect("enrollment-code")


@login_required
@require_POST
def device_pin_reset(request, device_id):
    require_owner(request)
    device = get_object_or_404(Device, pk=device_id)
    raw_pin = issue_kiosk_pin(device, request.user)
    request.session["one_time_kiosk_pin"] = {
        "device": device.label,
        "pin": raw_pin,
    }
    messages.success(request, "Kiosk administrator PIN reset.")
    return redirect("kiosk-pin")


@login_required
@require_POST
def device_disable(request, device_id):
    device = get_object_or_404(Device, pk=device_id)
    disable_device(device, request.user)
    messages.success(
        request,
        "Device disabled. It may authenticate only to receive maintenance state.",
    )
    return redirect("device-list")


@login_required
@require_POST
def device_reactivate(request, device_id):
    device = get_object_or_404(Device, pk=device_id)
    reactivate_device(device, request.user)
    messages.success(request, "Device explicitly reactivated.")
    return redirect("device-list")


@login_required
def enrollment_code(request):
    code = request.session.pop("one_time_enrollment_code", None)
    if not code:
        return redirect("device-list")
    return render(request, "signage/enrollment_code.html", code)


@login_required
def kiosk_pin(request):
    require_owner(request)
    pin = request.session.pop("one_time_kiosk_pin", None)
    if not pin:
        return redirect("device-list")
    return render(request, "signage/kiosk_pin.html", pin)


@login_required
@require_POST
def acknowledge_alert(request, alert_id):
    alert = get_object_or_404(Alert, pk=alert_id, acknowledged_at__isnull=True)
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
def settings_edit(request):
    require_owner(request)
    settings_object = PlatformSettings.load()
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
def user_edit(request, user_id=None):
    require_owner(request)
    model = get_user_model()
    user_object = get_object_or_404(model, pk=user_id) if user_id else model()
    form = DashboardUserForm(request.POST or None, instance=user_object)
    if request.method == "POST" and form.is_valid():
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
    events = PlaybackEvent.objects.select_related(
        "batch__device",
        "batch__assignment__vehicle",
        "batch__assignment__driver",
        "batch__playlist",
        "playlist_item__media",
    ).order_by("-started_at")
    AuditEvent.objects.create(
        actor=request.user,
        action="report.playback.export",
        target_type="playback_event",
        target_id="csv",
        metadata={"row_count": events.count()},
    )
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="proof-of-play.csv"'
    writer = csv.writer(response)
    writer.writerow(
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
        ]
    )
    for event in events.iterator():
        assignment = event.batch.assignment
        writer.writerow(
            [
                event.id,
                event.batch.device.label,
                assignment.vehicle.registration if assignment else "",
                assignment.driver.internal_id if assignment else "",
                str(event.batch.playlist),
                event.playlist_item.media.title,
                event.started_at.isoformat(),
                event.status,
                event.duration_ms,
                event.batch.captured_offline,
            ]
        )
    return response

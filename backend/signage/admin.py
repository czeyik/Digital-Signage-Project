from django.contrib import admin
from django.contrib.admin import AdminSite
from django.contrib.admin.forms import AdminAuthenticationForm, AdminPasswordChangeForm
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views.decorators.cache import never_cache

from .models import (
    Alert,
    AuditEvent,
    Device,
    DeviceAssignment,
    DeviceHeartbeat,
    DeviceOperationalEvent,
    Driver,
    EnrollmentChallenge,
    EnrollmentCode,
    HardwareQualification,
    MediaAsset,
    PlatformSettings,
    PlaybackBatch,
    PlaybackCorrection,
    PlaybackEvent,
    Playlist,
    User,
    Vehicle,
)
from .views import (
    AuditedLogoutView,
    AuditedPasswordChangeView,
    SecureAdminLoginView,
)


class OwnerAdminSite(AdminSite):
    """Small owner-only inspection surface for production operations."""

    site_header = "DUDU Car operations"
    site_title = "DUDU Car operations"
    index_title = "Operational records"

    def has_permission(self, request):
        return bool(
            request.user.is_active
            and request.user.is_staff
            and request.user.is_owner
        )

    @method_decorator(never_cache)
    @login_not_required
    def login(self, request, extra_context=None):
        if request.method == "GET" and self.has_permission(request):
            return HttpResponseRedirect(reverse("admin:index", current_app=self.name))
        if request.user.is_authenticated and not self.has_permission(request):
            raise PermissionDenied

        context = {
            **self.each_context(request),
            "title": _("Log in"),
            "subtitle": None,
            "app_path": request.get_full_path(),
            "username": request.user.get_username(),
        }
        if (
            REDIRECT_FIELD_NAME not in request.GET
            and REDIRECT_FIELD_NAME not in request.POST
        ):
            context[REDIRECT_FIELD_NAME] = reverse(
                "admin:index", current_app=self.name
            )
        context.update(extra_context or {})
        request.current_app = self.name
        return SecureAdminLoginView.as_view(
            extra_context=context,
            authentication_form=self.login_form or AdminAuthenticationForm,
            template_name=self.login_template or "admin/login.html",
            next_page=reverse("admin:index", current_app=self.name),
        )(request)

    def logout(self, request, extra_context=None):
        request.current_app = self.name
        return AuditedLogoutView.as_view(
            extra_context={
                **self.each_context(request),
                "has_permission": False,
                **(extra_context or {}),
            },
            template_name=self.logout_template or "registration/logged_out.html",
        )(request)

    def password_change(self, request, extra_context=None):
        request.current_app = self.name
        return AuditedPasswordChangeView.as_view(
            form_class=AdminPasswordChangeForm,
            success_url=reverse(
                "admin:password_change_done", current_app=self.name
            ),
            extra_context={
                **self.each_context(request),
                **(extra_context or {}),
            },
            template_name=self.password_change_template
            or "registration/password_change_form.html",
        )(request)


owner_admin_site = OwnerAdminSite(name="admin")


class OwnerOnlyAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return bool(getattr(request.user, "is_owner", False))

    def has_view_permission(self, request, obj=None):
        return bool(getattr(request.user, "is_owner", False))


class ReadOnlyAdmin(OwnerOnlyAdmin):
    """Expose protected records without creating an unaudited mutation path."""

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.concrete_fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(User, site=owner_admin_site)
class SignageUserAdmin(ReadOnlyAdmin):
    ordering = ("email",)
    list_display = ("email", "role", "is_active", "is_staff", "last_login")
    search_fields = ("email",)
    exclude = ("password", "groups", "user_permissions")


@admin.register(Driver, site=owner_admin_site)
class DriverAdmin(ReadOnlyAdmin):
    fields = ("internal_id", "name", "anonymized_at", "created_at", "updated_at")
    list_display = ("internal_id", "anonymized_at")
    search_fields = ("internal_id",)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        driver = self.get_object(request, object_id)
        if request.method == "GET" and driver is not None:
            AuditEvent.objects.create(
                actor=request.user,
                action="driver.personal_data.view",
                target_type=driver._meta.label_lower,
                target_id=str(driver.pk),
                metadata={"surface": "admin"},
            )
        return super().change_view(request, object_id, form_url, extra_context)


@admin.register(Vehicle, site=owner_admin_site)
class VehicleAdmin(ReadOnlyAdmin):
    list_display = ("registration", "anonymized_at")
    search_fields = ("registration",)


@admin.register(DeviceAssignment, site=owner_admin_site)
class DeviceAssignmentAdmin(ReadOnlyAdmin):
    list_display = (
        "device",
        "driver",
        "vehicle",
        "assigned_at",
        "unassigned_at",
    )
    list_select_related = ("device", "driver", "vehicle")


@admin.register(Device, site=owner_admin_site)
class DeviceAdmin(ReadOnlyAdmin):
    list_display = (
        "label",
        "status",
        "hardware_qualification",
        "last_seen_at",
        "last_sync_at",
        "app_version",
    )
    list_filter = ("status",)
    search_fields = ("label",)
    exclude = ("android_id_hash", "kiosk_pin_hash")


@admin.register(Playlist, site=owner_admin_site)
class PlaylistAdmin(ReadOnlyAdmin):
    list_display = ("name", "version", "status", "starts_at", "ends_at", "is_urgent")
    list_filter = ("status", "is_urgent")


@admin.register(MediaAsset, site=owner_admin_site)
class MediaAssetAdmin(ReadOnlyAdmin):
    list_display = ("title", "business_name", "kind", "status", "duration_ms")
    list_filter = ("kind", "status")
    search_fields = ("title", "business_name")


@admin.register(Alert, site=owner_admin_site)
class AlertAdmin(ReadOnlyAdmin):
    list_display = ("code", "device", "severity", "created_at", "acknowledged_at")
    list_filter = ("severity", "code")


@admin.register(PlatformSettings, site=owner_admin_site)
class PlatformSettingsAdmin(ReadOnlyAdmin):
    fields = (
        "playlist_max_entries",
        "playlist_max_duration_seconds",
        "created_at",
        "updated_at",
    )


@admin.register(HardwareQualification, site=owner_admin_site)
class HardwareQualificationAdmin(OwnerOnlyAdmin):
    list_display = (
        "model_name",
        "firmware_version",
        "android_version",
        "measured_display_diagonal_inches",
        "test_date",
        "approved_for_pilot",
    )
    list_filter = ("approved_for_pilot", "android_version")
    search_fields = ("model_name", "firmware_version", "evidence_reference")
    readonly_fields = ("approved_at",)

    def has_add_permission(self, request):
        return bool(getattr(request.user, "is_owner", False))

    def has_change_permission(self, request, obj=None):
        return bool(getattr(request.user, "is_owner", False))

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(
            actor=request.user,
            action=(
                "hardware_qualification.update"
                if change
                else "hardware_qualification.create"
            ),
            target_type=obj._meta.label_lower,
            target_id=str(obj.pk),
        )


@admin.register(AuditEvent, site=owner_admin_site)
class AuditEventAdmin(ReadOnlyAdmin):
    list_display = ("occurred_at", "actor", "action", "target_type", "target_id")
    list_filter = ("action", "target_type")


@admin.register(EnrollmentCode, site=owner_admin_site)
class EnrollmentCodeAdmin(ReadOnlyAdmin):
    list_display = ("device", "expires_at", "used_at", "created_by", "created_at")
    exclude = ("code_hash",)


@admin.register(EnrollmentChallenge, site=owner_admin_site)
class EnrollmentChallengeAdmin(ReadOnlyAdmin):
    list_display = ("enrollment", "expires_at", "used_at", "created_at")
    exclude = ("request_hash", "android_id_hash")


for model in (
    DeviceHeartbeat,
    DeviceOperationalEvent,
    PlaybackBatch,
    PlaybackEvent,
):
    owner_admin_site.register(model, ReadOnlyAdmin)


@admin.register(PlaybackCorrection, site=owner_admin_site)
class PlaybackCorrectionAdmin(OwnerOnlyAdmin):
    readonly_fields = ("created_by", "created_at")

    def has_add_permission(self, request):
        return bool(getattr(request.user, "is_owner", False))

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.created_by = request.user
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(
            actor=request.user,
            action="playback.correction.append",
            target_type=obj._meta.label_lower,
            target_id=str(obj.pk),
            metadata={"event": str(obj.event_id)},
        )

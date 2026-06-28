from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import (
    Alert,
    AuditEvent,
    Device,
    DeviceAssignment,
    DeviceHeartbeat,
    Driver,
    EnrollmentCode,
    HardwareQualification,
    MediaAsset,
    PlatformSettings,
    PlaybackBatch,
    PlaybackEvent,
    Playlist,
    PlaylistItem,
    User,
    Vehicle,
)


@admin.register(User)
class SignageUserAdmin(UserAdmin):
    ordering = ("email",)
    list_display = ("email", "role", "is_active", "is_staff")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Access", {"fields": ("role", "is_active", "is_staff", "is_superuser")}),
        ("Dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "role", "password1", "password2"),
            },
        ),
    )
    filter_horizontal = ()

    def has_module_permission(self, request):
        return request.user.is_owner

    def has_view_permission(self, request, obj=None):
        return request.user.is_owner

    def has_add_permission(self, request):
        return request.user.is_owner

    def has_change_permission(self, request, obj=None):
        return request.user.is_owner

    def has_delete_permission(self, request, obj=None):
        return request.user.is_owner


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ("internal_id", "anonymized_at")
    search_fields = ("internal_id", "name")

    def get_readonly_fields(self, request, obj=None):
        return () if request.user.is_owner else ("name",)

    def get_fields(self, request, obj=None):
        fields = list(super().get_fields(request, obj))
        if not request.user.is_owner and "name" in fields:
            fields.remove("name")
        return fields


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("registration", "anonymized_at")
    search_fields = ("registration",)


class AssignmentInline(admin.TabularInline):
    model = DeviceAssignment
    extra = 0


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("label", "status", "last_seen_at", "last_sync_at", "app_version")
    list_filter = ("status",)
    search_fields = ("label",)
    inlines = [AssignmentInline]
    readonly_fields = (
        "android_id_hash",
        "last_seen_at",
        "last_sync_at",
        "last_playback_at",
    )


class PlaylistItemInline(admin.TabularInline):
    model = PlaylistItem
    extra = 0
    ordering = ("position",)


@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "status", "starts_at", "ends_at", "is_urgent")
    list_filter = ("status", "is_urgent")
    inlines = [PlaylistItemInline]

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.status == Playlist.Status.PUBLISHED:
            return tuple(field.name for field in obj._meta.fields)
        return ("status", "published_at", "is_urgent")

    def get_inline_instances(self, request, obj=None):
        if obj and obj.status == Playlist.Status.PUBLISHED:
            return []
        return super().get_inline_instances(request, obj)


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ("title", "business_name", "kind", "status", "duration_ms")
    list_filter = ("kind", "status")
    search_fields = ("title", "business_name")
    readonly_fields = (
        "status",
        "normalized_file",
        "sha256",
        "mime_type",
        "file_size",
        "duration_ms",
        "width",
        "height",
        "rejection_reason",
    )


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("code", "device", "severity", "created_at", "acknowledged_at")
    list_filter = ("severity", "code")


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    fields = ("playlist_max_entries", "playlist_max_duration_seconds")

    def has_module_permission(self, request):
        return request.user.is_owner

    def has_view_permission(self, request, obj=None):
        return request.user.is_owner

    def has_add_permission(self, request):
        return request.user.is_owner and not PlatformSettings.objects.exists()

    def has_change_permission(self, request, obj=None):
        return request.user.is_owner

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HardwareQualification)
class HardwareQualificationAdmin(admin.ModelAdmin):
    list_display = (
        "model_name",
        "firmware_version",
        "android_version",
        "test_date",
        "approved_for_pilot",
    )
    list_filter = ("approved_for_pilot", "android_version")
    search_fields = ("model_name", "firmware_version", "evidence_reference")
    readonly_fields = ("approved_at",)

    def has_module_permission(self, request):
        return request.user.is_owner

    def has_view_permission(self, request, obj=None):
        return request.user.is_owner

    def has_add_permission(self, request):
        return request.user.is_owner

    def has_change_permission(self, request, obj=None):
        return request.user.is_owner

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class ImmutableAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class AuditEventAdmin(ImmutableAdmin):
    list_display = ("occurred_at", "actor", "action", "target_type", "target_id")
    readonly_fields = (
        "actor",
        "action",
        "target_type",
        "target_id",
        "metadata",
        "occurred_at",
        "source_ip_hash",
    )


for model in (EnrollmentCode, DeviceHeartbeat, PlaybackBatch, PlaybackEvent):
    admin.site.register(model, ImmutableAdmin)

import hashlib
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Q
from django.utils import timezone


def token_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# Keep the physical result inventory for optional operator observations. These
# fields no longer gate approval under the simplified qualification policy.
# ponytail: this intentionally permits approval before the full physical test
# suite is collected; reinstate per-field gates when the workflow can collect
# and review the complete evidence set again.
HARDWARE_QUALIFICATION_RECORDED_PASS_FIELDS = (
    "device_owner_lock_task_passed",
    "screen_state_passed",
    "battery_backed_playback_passed",
    "battery_runtime_passed",
    "battery_level_telemetry_passed",
    "planned_shutdown_flow_passed",
    "physical_shutdown_recovery_passed",
    "abnormal_exit_recovery_passed",
    "playback_12h_passed",
    "image_aspect_passed",
    "cache_capacity_passed",
    "network_reconnect_passed",
    "interrupted_download_passed",
    "thermal_passed",
    "mounting_power_safety_passed",
    "kiosk_escape_resistance_passed",
    "device_time_change_passed",
    "remote_disable_reboot_passed",
    "factory_reset_revocation_passed",
)

# Preserve the old import name for integrations that enumerate these fields.
HARDWARE_QUALIFICATION_REQUIRED_PASS_FIELDS = (
    HARDWARE_QUALIFICATION_RECORDED_PASS_FIELDS
)

MIN_QUALIFIED_DISPLAY_DIAGONAL_INCHES = Decimal("9.00")
MAX_QUALIFIED_DISPLAY_DIAGONAL_INCHES = Decimal("12.00")


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class PlatformSettings(TimeStampedModel):
    singleton_id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    playlist_max_entries = models.PositiveIntegerField(default=100)
    playlist_max_duration_seconds = models.PositiveIntegerField(default=1800)

    @classmethod
    def load(cls):
        settings_object, _ = cls.objects.get_or_create(singleton_id=1)
        return settings_object

    def clean(self):
        if self.singleton_id != 1:
            raise ValidationError("Only one platform settings record is allowed.")
        if self.playlist_max_entries < 1:
            raise ValidationError("Playlist entry limit must be positive.")
        if self.playlist_max_duration_seconds < 10:
            raise ValidationError(
                "Playlist duration limit must be at least 10 seconds."
            )

    def __str__(self):
        return "Pilot limits"


class HardwareQualification(TimeStampedModel):
    model_name = models.CharField(max_length=160)
    firmware_version = models.CharField(max_length=100)
    android_version = models.CharField(max_length=32)
    security_patch_level = models.CharField(max_length=32, blank=True)
    measured_display_diagonal_inches = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=(
            "Physical corner-to-corner display diagonal in inches, excluding bezel."
        ),
    )
    tested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    test_date = models.DateField()
    evidence_reference = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text=(
            "Optional internal path or ticket containing photos, logs, and test "
            "notes."
        ),
    )
    device_owner_lock_task_passed = models.BooleanField(default=False)
    # These two historic results describe the retired vehicle-power policy.  They
    # remain for auditability but cannot qualify hardware under the battery-backed
    # policy.
    legacy_boot_on_vehicle_power_passed = models.BooleanField(
        default=False,
        editable=False,
        # Keep the physical pre-policy column name for isolated, read-only
        # historic investigation with the pre-0010 image; that image is not a
        # supported live rollback target.
        db_column="boot_on_power_passed",
    )
    screen_state_passed = models.BooleanField(default=False)
    legacy_external_power_loss_path_passed = models.BooleanField(
        default=False,
        editable=False,
        # See legacy_boot_on_vehicle_power_passed above.
        db_column="power_loss_path_passed",
    )
    battery_backed_playback_passed = models.BooleanField(default=False)
    battery_runtime_passed = models.BooleanField(default=False)
    battery_level_telemetry_passed = models.BooleanField(default=False)
    planned_shutdown_flow_passed = models.BooleanField(default=False)
    physical_shutdown_recovery_passed = models.BooleanField(default=False)
    abnormal_exit_recovery_passed = models.BooleanField(default=False)
    playback_12h_passed = models.BooleanField(default=False)
    image_aspect_passed = models.BooleanField(default=False)
    cache_capacity_passed = models.BooleanField(default=False)
    network_reconnect_passed = models.BooleanField(default=False)
    interrupted_download_passed = models.BooleanField(default=False)
    thermal_passed = models.BooleanField(default=False)
    mounting_power_safety_passed = models.BooleanField(default=False)
    kiosk_escape_resistance_passed = models.BooleanField(default=False)
    device_time_change_passed = models.BooleanField(default=False)
    remote_disable_reboot_passed = models.BooleanField(default=False)
    factory_reset_revocation_passed = models.BooleanField(default=False)
    approved_for_pilot = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)

    # Compatibility name for callers that enumerate the optional observations.
    REQUIRED_PASS_FIELDS = HARDWARE_QUALIFICATION_RECORDED_PASS_FIELDS

    @property
    def is_enrollment_eligible(self):
        return (
            bool(self.model_name.strip())
            and bool(self.firmware_version.strip())
            and bool(self.security_patch_level.strip())
            and self.measured_display_diagonal_inches is not None
            and MIN_QUALIFIED_DISPLAY_DIAGONAL_INCHES
            <= self.measured_display_diagonal_inches
            <= MAX_QUALIFIED_DISPLAY_DIAGONAL_INCHES
        )

    # Approval attests to the exact physical build that was tested. A later
    # correction must be a new record, rather than rewriting that evidence.
    IMMUTABLE_AFTER_APPROVAL_FIELDS = (
        "model_name",
        "firmware_version",
        "android_version",
        "security_patch_level",
        "measured_display_diagonal_inches",
        "tested_by_id",
        "test_date",
        "evidence_reference",
        "legacy_boot_on_vehicle_power_passed",
        "screen_state_passed",
        "legacy_external_power_loss_path_passed",
        *HARDWARE_QUALIFICATION_RECORDED_PASS_FIELDS,
    )

    class Meta:
        ordering = ["-test_date", "model_name"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(approved_for_pilot=False)
                    | (
                        Q(measured_display_diagonal_inches__isnull=False)
                        & Q(
                            measured_display_diagonal_inches__gte=(
                                MIN_QUALIFIED_DISPLAY_DIAGONAL_INCHES
                            )
                        )
                        & Q(
                            measured_display_diagonal_inches__lte=(
                                MAX_QUALIFIED_DISPLAY_DIAGONAL_INCHES
                            )
                        )
                    )
                ),
                name="signage_hq_approved_display_size",
            ),
        ]

    def clean(self):
        if self.pk:
            original = HardwareQualification.objects.filter(pk=self.pk).first()
            if original and original.approved_at is not None:
                changed = [
                    field_name
                    for field_name in self.IMMUTABLE_AFTER_APPROVAL_FIELDS
                    if getattr(original, field_name) != getattr(self, field_name)
                ]
                if changed:
                    raise ValidationError(
                        "Approved hardware qualification evidence is immutable; "
                        "revoke it and create a fresh qualification record."
                    )
                if not original.approved_for_pilot and self.approved_for_pilot:
                    raise ValidationError(
                        "A revoked hardware qualification cannot be re-approved; "
                        "record fresh qualification evidence."
                    )
        if not self.approved_for_pilot:
            return
        if self.measured_display_diagonal_inches is None:
            raise ValidationError(
                {
                    "measured_display_diagonal_inches": (
                        "Approval requires a measured display diagonal."
                    )
                }
            )
        if not (
            MIN_QUALIFIED_DISPLAY_DIAGONAL_INCHES
            <= self.measured_display_diagonal_inches
            <= MAX_QUALIFIED_DISPLAY_DIAGONAL_INCHES
        ):
            raise ValidationError(
                {
                    "measured_display_diagonal_inches": (
                        "Approval requires a display diagonal from 9.00 to 12.00 "
                        "inches."
                    )
                }
            )
        if not self.security_patch_level:
            raise ValidationError(
                {
                    "security_patch_level": (
                        "Approval requires the verified Android security patch level."
                    )
                }
            )

    def save(self, *args, **kwargs):
        if self.approved_for_pilot and self.approved_at is None:
            self.approved_at = timezone.now()
        if not self.approved_for_pilot and self._state.adding:
            self.approved_at = None
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.model_name} / {self.firmware_version}"


class LoginThrottle(TimeStampedModel):
    key_hash = models.CharField(max_length=64, primary_key=True)
    failures = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    @property
    def is_locked(self):
        return self.locked_until is not None and self.locked_until > timezone.now()

    class Meta:
        indexes = [
            models.Index(fields=["updated_at"], name="signage_login_updated_idx")
        ]


class ApiThrottle(TimeStampedModel):
    key_hash = models.CharField(max_length=64, primary_key=True)
    attempts = models.PositiveIntegerField(default=0)
    window_started_at = models.DateTimeField(default=timezone.now)
    blocked_until = models.DateTimeField(null=True, blank=True)

    @property
    def is_blocked(self):
        return self.blocked_until is not None and self.blocked_until > timezone.now()

    class Meta:
        indexes = [models.Index(fields=["updated_at"], name="signage_api_updated_idx")]


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.full_clean()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.OWNER)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("A superuser must have staff and superuser enabled.")
        if extra_fields["role"] != User.Role.OWNER:
            raise ValueError("A superuser must be an account owner.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        OWNER = "owner", "Account owner"
        MARKETING = "marketing", "Marketing"

    username = None
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=16, choices=Role, default=Role.MARKETING)
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    def clean(self):
        super().clean()
        if self.email and not self.email.lower().endswith("@duducar.co"):
            raise ValidationError({"email": "Use a @duducar.co email address."})
        # Django staff status grants access to the administration application.
        # Marketing users use the purpose-built dashboard and must never inherit
        # that broader surface merely because they have a dashboard account.
        self.is_staff = self.is_owner
        if not self.is_owner:
            self.is_superuser = False

    @property
    def is_owner(self):
        return self.role == self.Role.OWNER

    def has_perm(self, perm, obj=None):
        if self.is_active and self.is_owner:
            return True
        if self.is_active and self.role == self.Role.MARKETING:
            app_label, _, codename = perm.partition(".")
            return app_label == "signage" and not codename.endswith("_user")
        return super().has_perm(perm, obj)

    def has_module_perms(self, app_label):
        if self.is_active and self.role in {self.Role.OWNER, self.Role.MARKETING}:
            return app_label == "signage"
        return super().has_module_perms(app_label)


class Driver(TimeStampedModel):
    internal_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=160)
    anonymized_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        # Driver names are owner-only; string representations appear in
        # foreign-key widgets and must not disclose the name to marketing users.
        return self.internal_id


class Vehicle(TimeStampedModel):
    registration = models.CharField(max_length=32, unique=True)
    anonymized_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.registration


class Playlist(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CANCELLED = "cancelled", "Cancelled"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=160)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status, default=Status.DRAFT)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    published_at = models.DateTimeField(null=True, blank=True)
    is_urgent = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_playlists",
    )
    superseded_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseded_versions",
        help_text="Replacement playlist version that cancelled this version.",
    )

    class Meta:
        ordering = ["-starts_at", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "version"], name="unique_playlist_name_version"
            ),
            models.CheckConstraint(
                condition=Q(ends_at__gt=models.F("starts_at")),
                name="playlist_end_after_start",
            ),
        ]

    @property
    def duration_seconds(self):
        return sum(item.duration_seconds for item in self.items.all())

    @property
    def window_state(self):
        if self.status != self.Status.PUBLISHED:
            return self.get_status_display()
        now = timezone.now()
        if self.starts_at <= now < self.ends_at:
            return "Active now"
        if self.starts_at > now:
            return "Scheduled"
        return "Ended"

    def clean(self):
        local_start = timezone.localtime(self.starts_at)
        if (
            local_start.weekday() != 0
            or local_start.hour != 12
            or local_start.minute != 0
            or local_start.second != 0
        ):
            raise ValidationError(
                {"starts_at": "Weekly playlists must begin Monday at 12:00 PM."}
            )
        if self.ends_at - self.starts_at != timedelta(days=7):
            raise ValidationError(
                {"ends_at": "Weekly playlists must cover exactly seven days."}
            )
        if self.pk:
            original = Playlist.objects.filter(pk=self.pk).first()
            if original:
                self._validate_locked_version(original)

    def _validate_locked_version(self, original):
        """Permit only the one-way published-to-cancelled correction transition."""
        locked_fields = (
            "name",
            "version",
            "starts_at",
            "ends_at",
            "published_at",
            "is_urgent",
            "created_by_id",
        )
        locked_fields_changed = any(
            getattr(original, field) != getattr(self, field) for field in locked_fields
        )
        if original.status == self.Status.CANCELLED:
            changed = locked_fields_changed or (
                self.status != original.status
                or self.superseded_by_id != original.superseded_by_id
            )
            if changed:
                raise ValidationError("Cancelled playlist versions are immutable.")
            return
        if original.status != self.Status.PUBLISHED:
            return
        if locked_fields_changed:
            raise ValidationError("Published playlist versions are immutable.")
        if self.status == self.Status.PUBLISHED:
            if self.superseded_by_id != original.superseded_by_id:
                raise ValidationError("Published playlist versions are immutable.")
            return
        if self.status != self.Status.CANCELLED or not self.superseded_by_id:
            raise ValidationError(
                "Published playlists can only be cancelled by a correction."
            )
        replacement = Playlist.objects.filter(pk=self.superseded_by_id).first()
        if not replacement or any(
            (
                replacement.status != self.Status.PUBLISHED,
                replacement.name != original.name,
                replacement.version <= original.version,
                replacement.starts_at != original.starts_at,
                replacement.ends_at != original.ends_at,
            )
        ):
            raise ValidationError(
                "A correction must link to a newer published version of the same "
                "playlist window."
            )

    def save(self, *args, **kwargs):
        if self.pk:
            original = Playlist.objects.filter(pk=self.pk).first()
            if original:
                self._validate_locked_version(original)
                if original.status == self.Status.CANCELLED:
                    raise ValidationError("Cancelled playlist versions are immutable.")
                if (
                    original.status == self.Status.PUBLISHED
                    and self.status == self.Status.PUBLISHED
                ):
                    raise ValidationError("Published playlist versions are immutable.")
                if (
                    original.status == self.Status.PUBLISHED
                    and self.status == self.Status.CANCELLED
                    and kwargs.get("update_fields") is not None
                    and not {"status", "superseded_by"}.issubset(
                        set(kwargs["update_fields"])
                    )
                ):
                    raise ValidationError(
                        "A correction must update status and replacement together."
                    )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.status in {self.Status.PUBLISHED, self.Status.CANCELLED}:
            raise ValidationError("Published playlist versions cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.name} v{self.version}"


def media_upload_path(instance, filename):
    return f"quarantine/{instance.id}/{filename}"


class MediaAsset(TimeStampedModel):
    class Kind(models.TextChoices):
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"

    class Status(models.TextChoices):
        QUARANTINED = "quarantined", "Quarantined"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        REJECTED = "rejected", "Rejected"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    business_name = models.CharField(max_length=160)
    title = models.CharField(max_length=160)
    kind = models.CharField(max_length=16, choices=Kind)
    status = models.CharField(max_length=16, choices=Status, default=Status.QUARANTINED)
    source_file = models.FileField(upload_to=media_upload_path)
    normalized_file = models.FileField(upload_to="validated/", null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    duration_ms = models.PositiveIntegerField(default=10_000)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    archived_at = models.DateTimeField(null=True, blank=True)
    dispatch_attempts = models.PositiveIntegerField(default=0)
    last_dispatch_attempt_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    processing_attempts = models.PositiveIntegerField(default=0)
    processing_token = models.UUIDField(null=True, blank=True, editable=False)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_lease_expires_at = models.DateTimeField(null=True, blank=True)
    processing_finished_at = models.DateTimeField(null=True, blank=True)

    def clean(self):
        if self.kind == self.Kind.IMAGE and self.duration_ms != 10_000:
            raise ValidationError(
                {"duration_ms": "Images must display for 10 seconds."}
            )
        if self.kind == self.Kind.VIDEO and self.duration_ms > 15_000:
            raise ValidationError({"duration_ms": "Videos cannot exceed 15 seconds."})

    def __str__(self):
        return f"{self.business_name}: {self.title}"


class PlaylistItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    playlist = models.ForeignKey(
        Playlist, on_delete=models.PROTECT, related_name="items"
    )
    media = models.ForeignKey(
        MediaAsset, on_delete=models.PROTECT, related_name="playlist_items"
    )
    position = models.PositiveIntegerField()

    class Meta:
        ordering = ["position"]
        constraints = [
            models.UniqueConstraint(
                fields=["playlist", "position"], name="unique_playlist_position"
            )
        ]

    @property
    def duration_seconds(self):
        return self.media.duration_ms / 1000

    def playlist_is_locked(self):
        return Playlist.objects.only("status").get(pk=self.playlist_id).status in {
            Playlist.Status.PUBLISHED,
            Playlist.Status.CANCELLED,
        }

    def save(self, *args, **kwargs):
        if self.playlist_id and self.playlist_is_locked():
            raise ValidationError("Published playlist items are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.playlist_is_locked():
            raise ValidationError("Published playlist items are immutable.")
        return super().delete(*args, **kwargs)


class Device(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending enrollment"
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    label = models.CharField(max_length=100, unique=True)
    hardware_qualification = models.ForeignKey(
        HardwareQualification,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="devices",
        help_text="Exact model and firmware qualification used for this device.",
    )
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    android_id_hash = models.CharField(
        max_length=64, blank=True, unique=True, null=True
    )
    hardware_model = models.CharField(max_length=160, blank=True)
    hardware_firmware_version = models.CharField(max_length=100, blank=True)
    hardware_security_patch = models.CharField(max_length=32, blank=True)
    app_version = models.CharField(max_length=32, blank=True)
    android_version = models.CharField(max_length=32, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_recorded_at = models.DateTimeField(null=True, blank=True)
    last_location_reported_at = models.DateTimeField(null=True, blank=True)
    location_state = models.CharField(max_length=32, default="initializing")
    location_state_updated_at = models.DateTimeField(null=True, blank=True)
    location_planned_gap_until = models.DateTimeField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_playback_at = models.DateTimeField(null=True, blank=True)
    current_playlist = models.ForeignKey(
        Playlist, null=True, blank=True, on_delete=models.PROTECT
    )
    disabled_at = models.DateTimeField(null=True, blank=True)
    kiosk_pin_hash = models.CharField(max_length=255, blank=True)
    kiosk_pin_reset_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.label


class DeviceAssignment(models.Model):
    device = models.ForeignKey(
        Device, on_delete=models.PROTECT, related_name="assignments"
    )
    driver = models.ForeignKey(Driver, on_delete=models.PROTECT)
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT)
    assigned_at = models.DateTimeField(default=timezone.now)
    unassigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-assigned_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["device"],
                condition=Q(unassigned_at__isnull=True),
                name="one_active_assignment_per_device",
            )
        ]


class EnrollmentCode(TimeStampedModel):
    device = models.ForeignKey(Device, on_delete=models.CASCADE)
    code_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    @classmethod
    def issue(cls, device, created_by):
        with transaction.atomic():
            locked_device = Device.objects.select_for_update().get(pk=device.pk)
            eligible_device = (
                locked_device.status != Device.Status.DISABLED
                and bool(locked_device.kiosk_pin_hash)
                and locked_device.assignments.filter(
                    unassigned_at__isnull=True
                ).exists()
            )
            if not eligible_device:
                raise ValidationError(
                    "Enrollment requires an enabled, assigned device with a kiosk "
                    "administrator PIN."
                )
            now = timezone.now()
            cls.objects.filter(
                device=locked_device,
                used_at__isnull=True,
            ).update(expires_at=now)
            for _ in range(10):
                raw = f"{secrets.randbelow(1_000_000):06d}"
                try:
                    # The unique hash is global. Keep the retry in a savepoint so
                    # a rare six-digit collision does not poison this transaction.
                    with transaction.atomic():
                        enrollment = cls.objects.create(
                            device=locked_device,
                            code_hash=token_hash(raw),
                            expires_at=now
                            + timedelta(seconds=settings.ENROLLMENT_CODE_TTL_SECONDS),
                            created_by=created_by,
                        )
                except IntegrityError:
                    continue
                break
            else:
                raise ValidationError("Could not issue a unique enrollment code.")
        return enrollment, raw

    class Meta:
        indexes = [
            models.Index(fields=["expires_at"], name="signage_enrollcode_exp_idx")
        ]

    @property
    def is_usable(self):
        return (
            self.used_at is None
            and self.expires_at > timezone.now()
            and bool(self.device.kiosk_pin_hash)
            and self.device.status != Device.Status.DISABLED
            and self.device.assignments.filter(unassigned_at__isnull=True).exists()
        )


class EnrollmentChallenge(TimeStampedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    enrollment = models.ForeignKey(
        EnrollmentCode, on_delete=models.CASCADE, related_name="challenges"
    )
    request_hash = models.CharField(max_length=64, unique=True)
    android_id_hash = models.CharField(max_length=64)
    android_version = models.CharField(max_length=32)
    app_version = models.CharField(max_length=32)
    hardware_model = models.CharField(max_length=160, blank=True)
    firmware_version = models.CharField(max_length=100, blank=True)
    security_patch_level = models.CharField(max_length=32, blank=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    @property
    def is_usable(self):
        return self.used_at is None and self.expires_at > timezone.now()

    class Meta:
        indexes = [
            models.Index(fields=["expires_at"], name="signage_enrollchall_exp_idx")
        ]


class DeviceCredential(TimeStampedModel):
    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name="credentials"
    )
    refresh_hash = models.CharField(max_length=64, unique=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    def issue(cls, device):
        raw = secrets.token_urlsafe(48)
        credential = cls.objects.create(device=device, refresh_hash=token_hash(raw))
        return credential, raw


class DeviceAccessToken(models.Model):
    credential = models.ForeignKey(
        DeviceCredential, on_delete=models.CASCADE, related_name="access_tokens"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, credential):
        raw = secrets.token_urlsafe(48)
        access = cls.objects.create(
            credential=credential,
            token_hash=token_hash(raw),
            expires_at=timezone.now()
            + timedelta(seconds=settings.DEVICE_ACCESS_TOKEN_TTL_SECONDS),
        )
        return access, raw

    class Meta:
        indexes = [
            models.Index(fields=["expires_at"], name="signage_access_token_exp_idx")
        ]


class MediaDeletion(TimeStampedModel):
    """Durable outbox entry for irreversible object-store removal."""

    asset = models.OneToOneField(
        MediaAsset,
        on_delete=models.PROTECT,
        related_name="binary_deletion",
    )
    source_name = models.CharField(max_length=255, blank=True)
    normalized_name = models.CharField(max_length=255, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)


class DeviceHeartbeat(models.Model):
    device = models.ForeignKey(
        Device, on_delete=models.PROTECT, related_name="heartbeats"
    )
    recorded_at = models.DateTimeField(default=timezone.now)
    received_at = models.DateTimeField(auto_now_add=True)
    screen_on = models.BooleanField()
    external_power = models.BooleanField(null=True, blank=True)
    charging = models.BooleanField(null=True, blank=True)
    battery_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    free_storage_bytes = models.PositiveBigIntegerField()
    temperature_celsius = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    app_version = models.CharField(max_length=32)
    android_version = models.CharField(max_length=32)
    active_playlist = models.ForeignKey(
        Playlist, null=True, blank=True, on_delete=models.PROTECT
    )
    playback_active = models.BooleanField(default=False)
    last_successful_sync_at = models.DateTimeField(null=True, blank=True)
    last_playback_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [models.Index(fields=["device", "-recorded_at"])]


class DeviceLocationPoint(models.Model):
    """One accepted, idempotent foreground location observation."""

    id = models.UUIDField(primary_key=True, editable=False)
    device = models.ForeignKey(
        Device,
        on_delete=models.PROTECT,
        related_name="location_points",
    )
    assignment = models.ForeignKey(
        DeviceAssignment,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="location_points",
    )
    recorded_at = models.DateTimeField()
    device_recorded_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    accuracy_m = models.DecimalField(max_digits=6, decimal_places=2)
    provider = models.CharField(max_length=16)
    source = models.CharField(max_length=32)

    class Meta:
        indexes = [
            models.Index(
                fields=["device", "-recorded_at"],
                name="signage_loc_dev_rec_idx",
            ),
            models.Index(fields=["recorded_at"], name="signage_loc_recorded_idx"),
        ]


class PlaybackBatch(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    device = models.ForeignKey(Device, on_delete=models.PROTECT)
    playlist = models.ForeignKey(Playlist, on_delete=models.PROTECT)
    assignment = models.ForeignKey(
        DeviceAssignment, null=True, on_delete=models.PROTECT
    )
    loop_started_at = models.DateTimeField()
    loop_ended_at = models.DateTimeField(null=True, blank=True)
    captured_offline = models.BooleanField(default=False)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["device", "-loop_started_at"])]


class PlaybackEvent(models.Model):
    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        INTERRUPTED = "interrupted", "Interrupted"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, editable=False)
    batch = models.ForeignKey(
        PlaybackBatch, on_delete=models.PROTECT, related_name="events"
    )
    playlist_item = models.ForeignKey(PlaylistItem, on_delete=models.PROTECT)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status)
    failure_reason = models.CharField(max_length=64, blank=True)

    class Meta:
        indexes = [models.Index(fields=["status", "-started_at"])]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Playback evidence is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Playback evidence cannot be deleted.")


class PlaybackCorrection(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(
        PlaybackEvent, on_delete=models.PROTECT, related_name="corrections"
    )
    reason = models.CharField(max_length=255)
    replacement_status = models.CharField(
        max_length=16, choices=PlaybackEvent.Status, null=True, blank=True
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Playback corrections are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Playback corrections are append-only.")


class DeviceOperationalEvent(models.Model):
    class Kind(models.TextChoices):
        FORCED_QUEUE_LOSS = "forced_queue_loss", "Forced queue data loss"
        REPLACEMENT_FAILED = "replacement_failed", "Replacement validation failed"
        PLANNED_SHUTDOWN = "planned_shutdown", "Planned shutdown"
        ABNORMAL_APP_EXIT = "abnormal_app_exit", "Abnormal application exit"
        LOCATION_QUEUE_LOSS = "location_queue_loss", "Location queue data loss"
        LOCATION_POINT_REJECTED = "location_point_rejected", "Location point rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    device = models.ForeignKey(
        Device, on_delete=models.PROTECT, related_name="operational_events"
    )
    kind = models.CharField(max_length=32, choices=Kind)
    recorded_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    details = models.JSONField(default=dict)

    class Meta:
        indexes = [
            models.Index(
                fields=["device", "kind", "-received_at"],
                name="signage_devop_kind_recv_idx",
            )
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Operational events are append-only.")
        return super().save(*args, **kwargs)


class Alert(TimeStampedModel):
    class Severity(models.TextChoices):
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    device = models.ForeignKey(
        Device, null=True, blank=True, on_delete=models.PROTECT, related_name="alerts"
    )
    code = models.CharField(max_length=64)
    severity = models.CharField(max_length=16, choices=Severity)
    message = models.CharField(max_length=255)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT
    )

    class Meta:
        indexes = [models.Index(fields=["acknowledged_at", "-created_at"])]
        constraints = [
            models.UniqueConstraint(
                fields=["device", "code"],
                condition=Q(
                    acknowledged_at__isnull=True,
                    device__isnull=False,
                ),
                name="signage_open_device_alert_unique",
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=Q(
                    acknowledged_at__isnull=True,
                    device__isnull=True,
                ),
                name="signage_open_global_alert_unique",
            ),
        ]


class AuditEvent(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT
    )
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=100)
    metadata = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(auto_now_add=True)
    source_ip_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-occurred_at"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Audit events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit events cannot be deleted.")

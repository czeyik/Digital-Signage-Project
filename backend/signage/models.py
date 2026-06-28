import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


def token_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
    tested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    test_date = models.DateField()
    evidence_reference = models.CharField(
        max_length=255,
        help_text="Internal path or ticket containing photos, logs, and test notes.",
    )
    device_owner_lock_task_passed = models.BooleanField(default=False)
    boot_on_power_passed = models.BooleanField(default=False)
    screen_state_passed = models.BooleanField(default=False)
    power_loss_path_passed = models.BooleanField(default=False)
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

    REQUIRED_PASS_FIELDS = (
        "device_owner_lock_task_passed",
        "boot_on_power_passed",
        "screen_state_passed",
        "power_loss_path_passed",
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

    class Meta:
        ordering = ["-test_date", "model_name"]

    def clean(self):
        if not self.approved_for_pilot:
            return
        missing = [
            self._meta.get_field(field_name).verbose_name
            for field_name in self.REQUIRED_PASS_FIELDS
            if not getattr(self, field_name)
        ]
        if missing:
            raise ValidationError(
                {
                    "approved_for_pilot": (
                        "All hardware qualification tests must pass before approval: "
                        + ", ".join(missing)
                    )
                }
            )
        if not self.evidence_reference:
            raise ValidationError(
                {"evidence_reference": "Approval requires an evidence reference."}
            )

    def save(self, *args, **kwargs):
        if self.approved_for_pilot and self.approved_at is None:
            self.approved_at = timezone.now()
        if not self.approved_for_pilot:
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
        self.is_staff = True

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
            if original and original.status == self.Status.PUBLISHED:
                changed = any(
                    getattr(original, field) != getattr(self, field)
                    for field in ("name", "version", "starts_at", "ends_at")
                )
                if changed:
                    raise ValidationError("Published playlist versions are immutable.")

    def save(self, *args, **kwargs):
        if self.pk:
            original = Playlist.objects.filter(pk=self.pk).first()
            if original and original.status == self.Status.PUBLISHED:
                mutable = (
                    "name",
                    "version",
                    "status",
                    "starts_at",
                    "ends_at",
                    "is_urgent",
                )
                if any(
                    getattr(original, field) != getattr(self, field)
                    for field in mutable
                ):
                    raise ValidationError("Published playlist versions are immutable.")
        return super().save(*args, **kwargs)

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

    def playlist_is_published(self):
        return (
            Playlist.objects.only("status").get(pk=self.playlist_id).status
            == Playlist.Status.PUBLISHED
        )

    def save(self, *args, **kwargs):
        if self.playlist_id and self.playlist_is_published():
            raise ValidationError("Published playlist items are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.playlist_is_published():
            raise ValidationError("Published playlist items are immutable.")
        return super().delete(*args, **kwargs)


class Device(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending enrollment"
        ACTIVE = "active", "Active"
        DISABLED = "disabled", "Disabled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    label = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=16, choices=Status, default=Status.PENDING)
    android_id_hash = models.CharField(
        max_length=64, blank=True, unique=True, null=True
    )
    app_version = models.CharField(max_length=32, blank=True)
    android_version = models.CharField(max_length=32, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_playback_at = models.DateTimeField(null=True, blank=True)
    current_playlist = models.ForeignKey(
        Playlist, null=True, blank=True, on_delete=models.PROTECT
    )
    disabled_at = models.DateTimeField(null=True, blank=True)
    kiosk_pin_hash = models.CharField(max_length=64, blank=True)
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
        raw = f"{secrets.randbelow(1_000_000):06d}"
        enrollment = cls.objects.create(
            device=device,
            code_hash=token_hash(raw),
            expires_at=timezone.now()
            + timedelta(seconds=settings.ENROLLMENT_CODE_TTL_SECONDS),
            created_by=created_by,
        )
        return enrollment, raw

    @property
    def is_usable(self):
        return self.used_at is None and self.expires_at > timezone.now()


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


class DeviceHeartbeat(models.Model):
    device = models.ForeignKey(
        Device, on_delete=models.PROTECT, related_name="heartbeats"
    )
    recorded_at = models.DateTimeField(default=timezone.now)
    received_at = models.DateTimeField(auto_now_add=True)
    screen_on = models.BooleanField()
    external_power = models.BooleanField()
    charging = models.BooleanField()
    battery_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    free_storage_bytes = models.PositiveBigIntegerField()
    temperature_celsius = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    app_version = models.CharField(max_length=32)
    android_version = models.CharField(max_length=32)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [models.Index(fields=["device", "-recorded_at"])]


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

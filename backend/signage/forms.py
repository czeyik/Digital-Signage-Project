import uuid
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    Device,
    DeviceAssignment,
    Driver,
    HardwareQualification,
    MediaAsset,
    PlatformSettings,
    Playlist,
    User,
    Vehicle,
)


class MediaUploadForm(forms.ModelForm):
    class Meta:
        model = MediaAsset
        fields = ["business_name", "title", "kind", "source_file"]

    def clean_source_file(self):
        uploaded = self.cleaned_data["source_file"]
        kind = self.cleaned_data.get("kind")
        limit = (
            settings.MEDIA_MAX_IMAGE_BYTES
            if kind == MediaAsset.Kind.IMAGE
            else settings.MEDIA_MAX_VIDEO_BYTES
        )
        if uploaded.size > limit:
            raise ValidationError("The uploaded file exceeds the allowed size.")
        extension = uploaded.name.lower().rsplit(".", 1)[-1]
        allowed = {"jpg", "jpeg", "png"} if kind == "image" else {"mp4"}
        if extension not in allowed:
            raise ValidationError("The filename does not match an accepted format.")
        return uploaded


class PlaylistForm(forms.ModelForm):
    media = forms.ModelMultipleChoiceField(
        queryset=MediaAsset.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Items initially follow this selection order and can be reordered in admin."
        ),
    )

    class Meta:
        model = Playlist
        fields = ["name", "version", "starts_at", "ends_at"]
        widgets = {
            "starts_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "ends_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["media"].queryset = MediaAsset.objects.filter(
            status=MediaAsset.Status.READY
        ).order_by("business_name", "title")


class DashboardUserForm(forms.ModelForm):
    password = forms.CharField(
        widget=forms.PasswordInput,
        required=False,
        help_text="Leave blank when editing to keep the current password.",
    )

    class Meta:
        model = User
        fields = ["email", "role", "is_active", "password"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ModelForm assigns the optional form field back to instance.password
        # during validation. Preserve the existing encoded hash when an owner
        # follows the documented "leave blank" edit path.
        self._original_password_hash = (
            self.instance.password if self.instance.pk else ""
        )

    def clean_password(self):
        password = self.cleaned_data.get("password")
        if password:
            validate_password(password, self.instance)
        elif not self.instance.pk:
            raise ValidationError("A password is required for new users.")
        return password

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        elif user.pk:
            user.password = self._original_password_hash
        if commit:
            user.full_clean()
            user.save()
        return user


class DeviceProvisioningForm(forms.Form):
    device_label = forms.CharField(label="Device Model & Number", max_length=100)
    hardware_qualification = forms.ModelChoiceField(
        queryset=HardwareQualification.objects.none(),
        required=False,
        help_text=(
            "Required before production enrollment; select the exact model, "
            "firmware, and security-patch registration with an attested "
            "9.00–12.00-inch display."
        ),
    )
    driver_name = forms.CharField(label="Driver Name", max_length=160)
    vehicle_registration = forms.CharField(
        label="Vehicle Registration Number", max_length=32
    )
    sim_card_number = forms.CharField(label="SIM Card Number", max_length=32)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["hardware_qualification"].queryset = (
            HardwareQualification.objects.filter(
                measured_display_diagonal_inches__gte=Decimal("9.00"),
                measured_display_diagonal_inches__lte=Decimal("12.00"),
            )
            .exclude(model_name="")
            .exclude(firmware_version="")
            .exclude(security_patch_level="")
            .order_by("model_name", "firmware_version")
        )

    def clean_device_label(self):
        label = self.cleaned_data["device_label"]
        if Device.objects.filter(label=label).exists():
            raise ValidationError("A device with this model and number already exists.")
        return label

    @transaction.atomic
    def save(self):
        driver = Driver.objects.create(
            internal_id=f"AUTO-{uuid.uuid4().hex}",
            name=self.cleaned_data["driver_name"],
        )
        vehicle, _ = Vehicle.objects.get_or_create(
            registration=self.cleaned_data["vehicle_registration"]
        )
        device = Device.objects.create(
            label=self.cleaned_data["device_label"],
            hardware_qualification=self.cleaned_data["hardware_qualification"],
        )
        DeviceAssignment.objects.create(
            device=device,
            driver=driver,
            vehicle=vehicle,
            sim_card_number=self.cleaned_data["sim_card_number"],
        )
        return device


class DeviceReassignmentForm(forms.Form):
    device_label = forms.CharField(label="Device Model & Number", max_length=100)
    driver_name = forms.CharField(label="Driver Name", max_length=160)
    vehicle_registration = forms.CharField(
        label="Vehicle Registration Number", max_length=32
    )
    sim_card_number = forms.CharField(label="SIM Card Number", max_length=32)

    def __init__(self, *args, device=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.device = device

    def clean_device_label(self):
        label = self.cleaned_data["device_label"]
        if Device.objects.exclude(pk=self.device.pk).filter(label=label).exists():
            raise ValidationError("A device with this model and number already exists.")
        return label

    @transaction.atomic
    def save(self, device):
        device.label = self.cleaned_data["device_label"]
        device.full_clean()
        device.save(update_fields=["label", "updated_at"])

        current_assignment = (
            DeviceAssignment.objects.select_for_update()
            .select_related("driver", "vehicle")
            .filter(device=device, unassigned_at__isnull=True)
            .first()
        )
        if current_assignment and (
            current_assignment.driver.name == self.cleaned_data["driver_name"]
            and current_assignment.vehicle.registration
            == self.cleaned_data["vehicle_registration"]
            and current_assignment.sim_card_number
            == self.cleaned_data["sim_card_number"]
        ):
            return False

        now = timezone.now()
        DeviceAssignment.objects.select_for_update().filter(
            device=device, unassigned_at__isnull=True
        ).update(unassigned_at=now)
        driver = Driver.objects.create(
            internal_id=f"AUTO-{uuid.uuid4().hex}",
            name=self.cleaned_data["driver_name"],
        )
        vehicle, _ = Vehicle.objects.get_or_create(
            registration=self.cleaned_data["vehicle_registration"]
        )
        DeviceAssignment.objects.create(
            device=device,
            driver=driver,
            vehicle=vehicle,
            sim_card_number=self.cleaned_data["sim_card_number"],
            assigned_at=now,
        )
        return True


class PlatformSettingsForm(forms.ModelForm):
    class Meta:
        model = PlatformSettings
        fields = ["playlist_max_entries", "playlist_max_duration_seconds"]

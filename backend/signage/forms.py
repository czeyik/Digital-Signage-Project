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
        if commit:
            user.full_clean()
            user.save()
        return user


class DeviceProvisioningForm(forms.Form):
    device_label = forms.CharField(max_length=100)
    hardware_qualification = forms.ModelChoiceField(
        queryset=HardwareQualification.objects.none(),
        required=False,
        help_text=(
            "Required before production enrollment; select the approved exact model "
            "and firmware qualification."
        ),
    )
    driver_internal_id = forms.CharField(max_length=64)
    driver_name = forms.CharField(max_length=160)
    vehicle_registration = forms.CharField(max_length=32)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["hardware_qualification"].queryset = (
            HardwareQualification.objects.filter(approved_for_pilot=True).order_by(
                "model_name", "firmware_version"
            )
        )

    @transaction.atomic
    def save(self):
        driver, _ = Driver.objects.get_or_create(
            internal_id=self.cleaned_data["driver_internal_id"],
            defaults={"name": self.cleaned_data["driver_name"]},
        )
        if driver.name != self.cleaned_data["driver_name"] and not driver.anonymized_at:
            driver.name = self.cleaned_data["driver_name"]
            driver.save(update_fields=["name", "updated_at"])
        vehicle, _ = Vehicle.objects.get_or_create(
            registration=self.cleaned_data["vehicle_registration"]
        )
        device = Device.objects.create(
            label=self.cleaned_data["device_label"],
            hardware_qualification=self.cleaned_data["hardware_qualification"],
        )
        DeviceAssignment.objects.create(device=device, driver=driver, vehicle=vehicle)
        return device


class DeviceReassignmentForm(forms.Form):
    driver_internal_id = forms.CharField(max_length=64)
    driver_name = forms.CharField(max_length=160)
    vehicle_registration = forms.CharField(max_length=32)

    @transaction.atomic
    def save(self, device):
        now = timezone.now()
        DeviceAssignment.objects.select_for_update().filter(
            device=device, unassigned_at__isnull=True
        ).update(unassigned_at=now)
        driver, _ = Driver.objects.get_or_create(
            internal_id=self.cleaned_data["driver_internal_id"],
            defaults={"name": self.cleaned_data["driver_name"]},
        )
        if driver.name != self.cleaned_data["driver_name"] and not driver.anonymized_at:
            driver.name = self.cleaned_data["driver_name"]
            driver.save(update_fields=["name", "updated_at"])
        vehicle, _ = Vehicle.objects.get_or_create(
            registration=self.cleaned_data["vehicle_registration"]
        )
        return DeviceAssignment.objects.create(
            device=device,
            driver=driver,
            vehicle=vehicle,
            assigned_at=now,
        )


class PlatformSettingsForm(forms.ModelForm):
    class Meta:
        model = PlatformSettings
        fields = ["playlist_max_entries", "playlist_max_duration_seconds"]

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError

from .models import MediaAsset, Playlist


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

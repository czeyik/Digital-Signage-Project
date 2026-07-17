import django.db.models.deletion
from django.db import migrations, models
from django.db.models import Count
from django.utils import timezone


def cancel_superseded_playlist_versions(apps, schema_editor):
    Playlist = apps.get_model("signage", "Playlist")
    duplicate_windows = (
        Playlist.objects.filter(status="published")
        .values("name", "starts_at", "ends_at")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
    )
    for window in duplicate_windows:
        versions = list(
            Playlist.objects.filter(
                status="published",
                name=window["name"],
                starts_at=window["starts_at"],
                ends_at=window["ends_at"],
            ).order_by("-version", "-published_at", "-created_at")
        )
        replacement = versions[0]
        for previous in versions[1:]:
            previous.status = "cancelled"
            previous.superseded_by_id = replacement.pk
            previous.updated_at = timezone.now()
            previous.save(update_fields=["status", "superseded_by", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("signage", "0006_production_hardening"),
    ]

    operations = [
        migrations.AlterField(
            model_name="playlist",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("published", "Published"),
                    ("cancelled", "Cancelled"),
                    ("archived", "Archived"),
                ],
                default="draft",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="playlist",
            name="superseded_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Replacement playlist version that cancelled this version.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="superseded_versions",
                to="signage.playlist",
            ),
        ),
        migrations.RunPython(
            cancel_superseded_playlist_versions, migrations.RunPython.noop
        ),
    ]

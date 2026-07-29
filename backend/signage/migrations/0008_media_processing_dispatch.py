import uuid
from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def mark_incomplete_processing_for_recovery(apps, schema_editor):
    media_asset = apps.get_model("signage", "MediaAsset")
    now = timezone.now()
    lease_expires_at = now + timedelta(hours=1)
    for asset in media_asset.objects.filter(status="processing").iterator():
        asset.processing_attempts = 1
        asset.processing_token = uuid.uuid4()
        asset.processing_started_at = now
        asset.processing_lease_expires_at = lease_expires_at
        asset.save(
            update_fields=[
                "processing_attempts",
                "processing_token",
                "processing_started_at",
                "processing_lease_expires_at",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("signage", "0007_playlist_supersession"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediaasset",
            name="dispatch_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="last_dispatch_attempt_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="dispatched_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="processing_attempts",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="processing_token",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="processing_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="processing_lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mediaasset",
            name="processing_finished_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            mark_incomplete_processing_for_recovery,
            migrations.RunPython.noop,
        ),
    ]

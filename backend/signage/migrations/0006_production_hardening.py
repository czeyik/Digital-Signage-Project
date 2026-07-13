import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("signage", "0005_hardwarequalification"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="hardware_qualification",
            field=models.ForeignKey(
                blank=True,
                help_text="Exact model and firmware qualification used for this device.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="devices",
                to="signage.hardwarequalification",
            ),
        ),
        migrations.AlterField(
            model_name="device",
            name="kiosk_pin_hash",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.CreateModel(
            name="ApiThrottle",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "key_hash",
                    models.CharField(max_length=64, primary_key=True, serialize=False),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
                (
                    "window_started_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                ("blocked_until", models.DateTimeField(blank=True, null=True)),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="EnrollmentChallenge",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("request_hash", models.CharField(max_length=64, unique=True)),
                ("android_id_hash", models.CharField(max_length=64)),
                ("android_version", models.CharField(max_length=32)),
                ("app_version", models.CharField(max_length=32)),
                ("expires_at", models.DateTimeField()),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                (
                    "enrollment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="challenges",
                        to="signage.enrollmentcode",
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.AddField(
            model_name="deviceheartbeat",
            name="active_playlist",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to="signage.playlist",
            ),
        ),
        migrations.AddField(
            model_name="deviceheartbeat",
            name="last_playback_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="deviceheartbeat",
            name="last_successful_sync_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="deviceheartbeat",
            name="playback_active",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="PlaybackCorrection",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("reason", models.CharField(max_length=255)),
                (
                    "replacement_status",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("completed", "Completed"),
                            ("interrupted", "Interrupted"),
                            ("failed", "Failed"),
                        ],
                        max_length=16,
                        null=True,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="corrections",
                        to="signage.playbackevent",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="DeviceOperationalEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("forced_queue_loss", "Forced queue data loss"),
                            ("replacement_failed", "Replacement validation failed"),
                        ],
                        max_length=32,
                    ),
                ),
                ("recorded_at", models.DateTimeField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("details", models.JSONField(default=dict)),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="operational_events",
                        to="signage.device",
                    ),
                ),
            ],
        ),
    ]

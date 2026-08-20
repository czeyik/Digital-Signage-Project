import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models
from django.db.models import Min


def acknowledge_duplicate_open_alerts(apps, schema_editor):
    """Retain one open alert per key before adding the partial constraints."""

    Alert = apps.get_model("signage", "Alert")
    now = django.utils.timezone.now()
    groups = (
        Alert.objects.filter(acknowledged_at__isnull=True)
        .values("device_id", "code")
        .annotate(first_id=Min("id"))
    )
    for group in groups.iterator():
        alerts = Alert.objects.filter(
            acknowledged_at__isnull=True,
            code=group["code"],
        )
        if group["device_id"] is None:
            alerts = alerts.filter(device_id__isnull=True)
        else:
            alerts = alerts.filter(device_id=group["device_id"])
        duplicate_ids = list(
            alerts.exclude(pk=group["first_id"]).values_list("pk", flat=True)
        )
        if not duplicate_ids:
            continue
        critical = alerts.filter(severity="critical").order_by("created_at").first()
        if critical and critical.pk != group["first_id"]:
            Alert.objects.filter(pk=group["first_id"]).update(
                severity="critical",
                message=critical.message,
                updated_at=now,
            )
        Alert.objects.filter(pk__in=duplicate_ids).update(
            acknowledged_at=now,
            updated_at=now,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("signage", "0011_restore_hardware_rollback_columns"),
    ]

    operations = [
        migrations.AddField(
            model_name="hardwarequalification",
            name="security_patch_level",
            field=models.CharField(blank=True, default="", max_length=32),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="device",
            name="hardware_firmware_version",
            field=models.CharField(blank=True, default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="device",
            name="hardware_model",
            field=models.CharField(blank=True, default="", max_length=160),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="device",
            name="hardware_security_patch",
            field=models.CharField(blank=True, default="", max_length=32),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="device",
            name="last_heartbeat_recorded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="enrollmentchallenge",
            name="firmware_version",
            field=models.CharField(blank=True, default="", max_length=100),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="enrollmentchallenge",
            name="hardware_model",
            field=models.CharField(blank=True, default="", max_length=160),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="enrollmentchallenge",
            name="security_patch_level",
            field=models.CharField(blank=True, default="", max_length=32),
            preserve_default=False,
        ),
        migrations.CreateModel(
            name="MediaDeletion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("source_name", models.CharField(blank=True, max_length=255)),
                ("normalized_name", models.CharField(blank=True, max_length=255)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("last_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, max_length=255)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "asset",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="binary_deletion",
                        to="signage.mediaasset",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="loginthrottle",
            index=models.Index(fields=["updated_at"], name="signage_login_updated_idx"),
        ),
        migrations.AddIndex(
            model_name="apithrottle",
            index=models.Index(fields=["updated_at"], name="signage_api_updated_idx"),
        ),
        migrations.AddIndex(
            model_name="enrollmentcode",
            index=models.Index(fields=["expires_at"], name="signage_enrollcode_exp_idx"),
        ),
        migrations.AddIndex(
            model_name="enrollmentchallenge",
            index=models.Index(fields=["expires_at"], name="signage_enrollchall_exp_idx"),
        ),
        migrations.AddIndex(
            model_name="deviceaccesstoken",
            index=models.Index(fields=["expires_at"], name="signage_access_token_exp_idx"),
        ),
        migrations.RunPython(
            acknowledge_duplicate_open_alerts,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="alert",
            constraint=models.UniqueConstraint(
                condition=models.Q(acknowledged_at__isnull=True, device__isnull=False),
                fields=("device", "code"),
                name="signage_open_device_alert_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="alert",
            constraint=models.UniqueConstraint(
                condition=models.Q(acknowledged_at__isnull=True, device__isnull=True),
                fields=("code",),
                name="signage_open_global_alert_unique",
            ),
        ),
    ]

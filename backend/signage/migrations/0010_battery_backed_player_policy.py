from django.db import migrations, models
from django.utils import timezone


def invalidate_legacy_hardware_approvals(apps, schema_editor):
    """Require fresh evidence for the battery-backed qualification policy."""

    HardwareQualification = apps.get_model("signage", "HardwareQualification")
    HardwareQualification.objects.filter(approved_for_pilot=True).update(
        approved_for_pilot=False,
        approved_at=None,
        updated_at=timezone.now(),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("signage", "0009_revoke_marketing_admin_access"),
    ]

    operations = [
        migrations.RenameField(
            model_name="hardwarequalification",
            old_name="boot_on_power_passed",
            new_name="legacy_boot_on_vehicle_power_passed",
        ),
        migrations.RenameField(
            model_name="hardwarequalification",
            old_name="power_loss_path_passed",
            new_name="legacy_external_power_loss_path_passed",
        ),
        migrations.AlterField(
            model_name="hardwarequalification",
            name="legacy_boot_on_vehicle_power_passed",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AlterField(
            model_name="hardwarequalification",
            name="legacy_external_power_loss_path_passed",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="hardwarequalification",
            name="abnormal_exit_recovery_passed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="hardwarequalification",
            name="battery_backed_playback_passed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="hardwarequalification",
            name="battery_level_telemetry_passed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="hardwarequalification",
            name="battery_runtime_passed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="hardwarequalification",
            name="physical_shutdown_recovery_passed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="hardwarequalification",
            name="planned_shutdown_flow_passed",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(
            invalidate_legacy_hardware_approvals,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="deviceheartbeat",
            name="charging",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="deviceheartbeat",
            name="external_power",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="deviceoperationalevent",
            name="kind",
            field=models.CharField(
                choices=[
                    ("forced_queue_loss", "Forced queue data loss"),
                    ("replacement_failed", "Replacement validation failed"),
                    ("planned_shutdown", "Planned shutdown"),
                    ("abnormal_app_exit", "Abnormal application exit"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddIndex(
            model_name="deviceoperationalevent",
            index=models.Index(
                fields=["device", "kind", "-received_at"],
                name="signage_devop_kind_recv_idx",
            ),
        ),
    ]

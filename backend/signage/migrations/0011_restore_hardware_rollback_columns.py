from django.db import migrations, models
from django.utils import timezone

# Version c83253b of migration 0010 used RenameField database operations.  The
# release migration keeps this repair so any development or pre-production
# database that applied that version is brought back to the rollback-compatible
# physical column names.  Fresh databases need no change because the corrected
# 0010 now changes only migration state.
ROLLBACK_COMPATIBLE_COLUMNS = (
    (
        "legacy_boot_on_vehicle_power_passed",
        "boot_on_power_passed",
    ),
    (
        "legacy_external_power_loss_path_passed",
        "power_loss_path_passed",
    ),
)

BATTERY_POLICY_REQUIRED_FIELDS = (
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


def restore_rollback_compatible_hardware_columns(apps, schema_editor):
    """Restore pre-policy physical names without changing historic values.

    Current code calls these fields ``legacy_*`` but maps them with
    ``db_column`` to the pre-0010 names.  Failing closed on an unexpected
    schema avoids choosing between two potentially divergent historic values.
    """

    HardwareQualification = apps.get_model("signage", "HardwareQualification")
    table_name = HardwareQualification._meta.db_table
    quote_name = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        column_names = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, table_name
            )
        }

    renames = []
    for current_name, rollback_name in ROLLBACK_COMPATIBLE_COLUMNS:
        has_current_name = current_name in column_names
        has_rollback_name = rollback_name in column_names
        if has_current_name and has_rollback_name:
            raise RuntimeError(
                "Hardware qualification rollback compatibility cannot be restored "
                f"because both {current_name!r} and {rollback_name!r} exist."
            )
        if not has_current_name and not has_rollback_name:
            raise RuntimeError(
                "Hardware qualification rollback compatibility cannot be restored "
                f"because neither {current_name!r} nor {rollback_name!r} exists."
            )
        if has_current_name:
            renames.append((current_name, rollback_name))

    for current_name, rollback_name in renames:
        schema_editor.execute(
            (
                "ALTER TABLE {table_name} RENAME COLUMN "
                "{current_name} TO {rollback_name}"
            ).format(
                table_name=quote_name(table_name),
                current_name=quote_name(current_name),
                rollback_name=quote_name(rollback_name),
            )
        )


def invalidate_non_battery_hardware_approvals(apps, schema_editor):
    """Ensure a previously deployed 0010 cannot leave legacy approvals live."""

    HardwareQualification = apps.get_model("signage", "HardwareQualification")
    HardwareQualification.objects.filter(approved_for_pilot=True).exclude(
        **{field_name: True for field_name in BATTERY_POLICY_REQUIRED_FIELDS}
    ).update(
        approved_for_pilot=False,
        approved_at=None,
        updated_at=timezone.now(),
    )


class Migration(migrations.Migration):
    dependencies = [
        ("signage", "0010_battery_backed_player_policy"),
    ]

    operations = [
        migrations.RunPython(
            restore_rollback_compatible_hardware_columns,
            # Corrected 0010 also uses the pre-policy physical names, so no
            # schema change is required when this release migration is reversed.
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RunPython(
            invalidate_non_battery_hardware_approvals,
            reverse_code=migrations.RunPython.noop,
        ),
        # Enforce the new qualification policy below the ORM.  A pre-0010
        # image can still read historic rows through the compatibility columns,
        # but cannot re-approve hardware using retired vehicle-power checks.
        migrations.AddConstraint(
            model_name="hardwarequalification",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(approved_for_pilot=False)
                    | models.Q(
                        **{
                            field_name: True
                            for field_name in BATTERY_POLICY_REQUIRED_FIELDS
                        }
                    )
                ),
                name="signage_hq_battery_policy_approved",
            ),
        ),
    ]

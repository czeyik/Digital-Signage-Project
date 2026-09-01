import os
import subprocess
import sys
from textwrap import dedent


def test_hardware_qualification_schema_supports_old_image_rollback(tmp_path):
    """Rehearse 0009 -> 0011 and prove the old ORM can still read the row.

    This runs in an isolated SQLite database in a child process so data
    migrations use that database's ``default`` alias and do not perturb the
    pytest database.  It also simulates a database that ran the original 0010
    column rename, exercising the repair path in 0011.
    """

    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    database_path = tmp_path / "hardware-migration-rehearsal.sqlite3"
    settings_path = tmp_path / "migration_rehearsal_settings.py"
    settings_path.write_text(
        dedent(
            f"""
            from config.settings import *

            DATABASES = {{
                "default": {{
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": {str(database_path)!r},
                }}
            }}
            """
        ),
        encoding="utf-8",
    )
    rehearsal = dedent(
        """
        from datetime import date, datetime, timedelta, timezone
        import uuid

        import django

        django.setup()

        from django.db import IntegrityError, connection, models
        from django.db.migrations.executor import MigrationExecutor

        old_target = [("signage", "0009_revoke_marketing_admin_access")]
        policy_target = [("signage", "0010_battery_backed_player_policy")]
        new_target = [("signage", "0011_restore_hardware_rollback_columns")]

        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        old_apps = executor.loader.project_state(old_target).apps
        OldUser = old_apps.get_model("signage", "User")
        OldHardwareQualification = old_apps.get_model(
            "signage", "HardwareQualification"
        )
        owner = OldUser.objects.create(
            email="rollback-owner@duducar.co",
            password="not-used-for-this-rehearsal",
            role="owner",
        )
        qualification = OldHardwareQualification.objects.create(
            model_name="Rollback-compatible tablet",
            firmware_version="1.0",
            android_version="13",
            tested_by=owner,
            test_date=date(2026, 8, 9),
            evidence_reference="internal://release/rehearsal",
            boot_on_power_passed=True,
            power_loss_path_passed=True,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(policy_target)
        # This is a legacy-image update after the original 0010 had invalidated
        # prior approvals.  It must be cleared by the 0011 policy constraint.
        OldHardwareQualification.objects.filter(pk=qualification.pk).update(
            approved_for_pilot=True
        )

        PolicyHardwareQualification = executor.loader.project_state(
            policy_target
        ).apps.get_model("signage", "HardwareQualification")
        table_name = PolicyHardwareQualification._meta.db_table
        # Simulate a development database that applied the original 0010,
        # which physically renamed the two columns.  Applying 0011 must repair
        # this schema and invalidate the legacy-only re-approval.
        with connection.schema_editor() as schema_editor:
            quote_name = schema_editor.quote_name
            schema_editor.execute(
                (
                    "ALTER TABLE {table_name} RENAME COLUMN "
                    "{old_name} TO {new_name}"
                ).format(
                    table_name=quote_name(table_name),
                    old_name=quote_name("boot_on_power_passed"),
                    new_name=quote_name("legacy_boot_on_vehicle_power_passed"),
                )
            )
            schema_editor.execute(
                (
                    "ALTER TABLE {table_name} RENAME COLUMN "
                    "{old_name} TO {new_name}"
                ).format(
                    table_name=quote_name(table_name),
                    old_name=quote_name("power_loss_path_passed"),
                    new_name=quote_name("legacy_external_power_loss_path_passed"),
                )
            )
        executor = MigrationExecutor(connection)
        executor.migrate(new_target)
        new_apps = executor.loader.project_state(new_target).apps
        NewHardwareQualification = new_apps.get_model(
            "signage", "HardwareQualification"
        )

        repaired_current_row = NewHardwareQualification.objects.get(
            pk=qualification.pk
        )
        repaired_old_image_row = OldHardwareQualification.objects.get(
            pk=qualification.pk
        )
        assert repaired_current_row.legacy_boot_on_vehicle_power_passed is True
        assert repaired_current_row.legacy_external_power_loss_path_passed is True
        assert repaired_current_row.approved_for_pilot is False
        assert repaired_old_image_row.boot_on_power_passed is True
        assert repaired_old_image_row.power_loss_path_passed is True
        try:
            OldHardwareQualification.objects.filter(pk=qualification.pk).update(
                approved_for_pilot=True
            )
        except IntegrityError:
            pass
        else:
            raise AssertionError(
                "The pre-policy ORM must not be able to re-approve hardware."
            )

        with connection.cursor() as cursor:
            column_names = {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, table_name
                )
            }
        assert "boot_on_power_passed" in column_names
        assert "power_loss_path_passed" in column_names
        assert "legacy_boot_on_vehicle_power_passed" not in column_names
        assert "legacy_external_power_loss_path_passed" not in column_names

        # A full schema rollback retains the original physical names as well.
        executor = MigrationExecutor(connection)
        executor.migrate(old_target)
        rollback_row = OldHardwareQualification.objects.get(pk=qualification.pk)
        assert rollback_row.boot_on_power_passed is True
        assert rollback_row.power_loss_path_passed is True

        pre_display_target = [("signage", "0012_backend_hardening")]
        display_target = [("signage", "0013_display_diagonal_qualification")]
        executor = MigrationExecutor(connection)
        executor.migrate(pre_display_target)
        pre_display_apps = executor.loader.project_state(pre_display_target).apps
        PreDisplayUser = pre_display_apps.get_model("signage", "User")
        PreDisplayQualification = pre_display_apps.get_model(
            "signage", "HardwareQualification"
        )
        display_owner = PreDisplayUser.objects.create(
            email="display-policy-owner@duducar.co",
            password="not-used-for-this-rehearsal",
            role="owner",
        )
        display_pass_fields = {
            field.name: True
            for field in PreDisplayQualification._meta.fields
            if isinstance(field, models.BooleanField)
            and field.name != "approved_for_pilot"
        }
        unmeasured_approval = PreDisplayQualification.objects.create(
            model_name="Unmeasured approved tablet",
            firmware_version="1.0",
            android_version="13",
            security_patch_level="2026-08-05",
            tested_by=display_owner,
            test_date=date(2026, 8, 9),
            evidence_reference="internal://release/display-policy-rehearsal",
            approved_for_pilot=True,
            approved_at=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
            **display_pass_fields,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(display_target)
        display_apps = executor.loader.project_state(display_target).apps
        DisplayQualification = display_apps.get_model(
            "signage", "HardwareQualification"
        )
        revoked_approval = DisplayQualification.objects.get(pk=unmeasured_approval.pk)
        assert revoked_approval.approved_for_pilot is False
        assert revoked_approval.approved_at == datetime(
            2026, 8, 9, 12, 0, tzinfo=timezone.utc
        )
        try:
            DisplayQualification.objects.filter(pk=revoked_approval.pk).update(
                approved_for_pilot=True
            )
        except IntegrityError:
            pass
        else:
            raise AssertionError(
                "The display policy must block re-approval without a measurement."
            )

        final_target = [("signage", "0015_simplify_hardware_qualification")]
        executor = MigrationExecutor(connection)
        executor.migrate(final_target)
        final_apps = executor.loader.project_state(final_target).apps
        FinalDevice = final_apps.get_model("signage", "Device")
        FinalLocationPoint = final_apps.get_model("signage", "DeviceLocationPoint")
        gps_device = FinalDevice.objects.create(
            label="GPS migration rehearsal",
            status="active",
            app_version="1.0.0",
            android_version="13",
        )
        recorded_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        location_point = FinalLocationPoint.objects.create(
            id=uuid.uuid4(),
            device=gps_device,
            recorded_at=recorded_at,
            device_recorded_at=recorded_at,
            latitude="3.139000",
            longitude="101.686900",
            accuracy_m="12.50",
            provider="gps",
            source="location_manager",
        )
        assert gps_device.location_state == "initializing"
        assert (
            FinalLocationPoint.objects.get(pk=location_point.pk).device_id
            == gps_device.pk
        )

        management_target = [("signage", "0016_device_management")]
        executor = MigrationExecutor(connection)
        executor.migrate(management_target)
        management_apps = executor.loader.project_state(management_target).apps
        ManagedDevice = management_apps.get_model("signage", "Device")
        ManagedUser = management_apps.get_model("signage", "User")
        ManagementCredential = management_apps.get_model(
            "signage", "DeviceManagementCredential"
        )
        DeviceCommand = management_apps.get_model("signage", "DeviceCommand")
        migrated_device = ManagedDevice.objects.get(pk=gps_device.pk)
        migrated_owner = ManagedUser.objects.get(pk=display_owner.pk)
        management_credential = ManagementCredential.objects.create(
            device=migrated_device,
            token_hash="a" * 64,
        )
        command = DeviceCommand.objects.create(
            device=migrated_device,
            kind="admin_mode",
            requested_by=migrated_owner,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        assert management_credential.device_id == gps_device.pk
        assert command.device_id == gps_device.pk
        assert command.requested_by_id == display_owner.pk
        assert (
            management_apps.get_model("signage", "DeviceLocationPoint")
            .objects.get(pk=location_point.pk)
            .device_id
            == gps_device.pk
        )

        LegacyMediaAsset = management_apps.get_model("signage", "MediaAsset")
        legacy_image = LegacyMediaAsset.objects.create(
            business_name="Migration rehearsal",
            title="Existing image",
            kind="image",
            status="ready",
            source_file="quarantine/existing-image.png",
            normalized_file="validated/existing-image.png",
            duration_ms=10_000,
            uploaded_by=migrated_owner,
        )
        legacy_video = LegacyMediaAsset.objects.create(
            business_name="Migration rehearsal",
            title="Existing video",
            kind="video",
            status="ready",
            source_file="quarantine/existing-video.mp4",
            normalized_file="validated/existing-video.mp4",
            duration_ms=4_333,
            uploaded_by=migrated_owner,
        )

        image_duration_target = [("signage", "0017_image_duration_15_seconds")]
        executor = MigrationExecutor(connection)
        executor.migrate(image_duration_target)
        image_duration_apps = executor.loader.project_state(
            image_duration_target
        ).apps
        MigratedMediaAsset = image_duration_apps.get_model("signage", "MediaAsset")
        assert MigratedMediaAsset.objects.get(pk=legacy_image.pk).duration_ms == 15_000
        assert MigratedMediaAsset.objects.get(pk=legacy_video.pk).duration_ms == 4_333
        """
    )
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "migration_rehearsal_settings",
        "PYTHONPATH": os.pathsep.join(
            value
            for value in (
                str(tmp_path),
                backend_dir,
                os.environ.get("PYTHONPATH"),
            )
            if value
        ),
    }
    result = subprocess.run(  # noqa: S603 - fixed local interpreter and rehearsal
        [sys.executable, "-c", rehearsal],
        cwd=backend_dir,
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr

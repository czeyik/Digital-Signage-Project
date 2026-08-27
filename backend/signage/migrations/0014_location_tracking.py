from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("signage", "0013_display_diagonal_qualification"),
    ]

    operations = [
        migrations.AddField(
            model_name="device",
            name="last_location_reported_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="device",
            name="location_planned_gap_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="device",
            name="location_state",
            field=models.CharField(default="initializing", max_length=32),
        ),
        migrations.AddField(
            model_name="device",
            name="location_state_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="DeviceLocationPoint",
            fields=[
                (
                    "id",
                    models.UUIDField(editable=False, primary_key=True, serialize=False),
                ),
                ("recorded_at", models.DateTimeField()),
                ("device_recorded_at", models.DateTimeField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("latitude", models.DecimalField(decimal_places=6, max_digits=9)),
                ("longitude", models.DecimalField(decimal_places=6, max_digits=9)),
                ("accuracy_m", models.DecimalField(decimal_places=2, max_digits=6)),
                ("provider", models.CharField(max_length=16)),
                ("source", models.CharField(max_length=32)),
                (
                    "assignment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="location_points",
                        to="signage.deviceassignment",
                    ),
                ),
                (
                    "device",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="location_points",
                        to="signage.device",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["device", "-recorded_at"],
                        name="signage_loc_dev_rec_idx",
                    ),
                    models.Index(
                        fields=["recorded_at"], name="signage_loc_recorded_idx"
                    ),
                ],
            },
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
                    ("location_queue_loss", "Location queue data loss"),
                    ("location_point_rejected", "Location point rejected"),
                ],
                max_length=32,
            ),
        ),
    ]

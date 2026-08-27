from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("signage", "0012_backend_hardening"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hardwarequalification",
            name="evidence_reference",
            field=models.CharField(
                blank=True,
                default="",
                help_text=(
                    "Internal path or ticket containing photos, logs, and test notes."
                ),
                max_length=255,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="hardwarequalification",
            name="signage_hq_battery_policy_approved",
        ),
    ]

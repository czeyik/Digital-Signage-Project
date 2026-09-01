from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("signage", "0017_image_duration_15_seconds"),
    ]

    operations = [
        migrations.AlterField(
            model_name="devicecommand",
            name="kind",
            field=models.CharField(
                choices=[
                    ("admin_mode", "Admin mode"),
                    ("sync_now", "Sync now"),
                ],
                max_length=32,
            ),
        ),
    ]

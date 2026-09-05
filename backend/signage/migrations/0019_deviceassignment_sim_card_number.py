from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("signage", "0018_device_sync_command"),
    ]

    operations = [
        migrations.AddField(
            model_name="deviceassignment",
            name="sim_card_number",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]

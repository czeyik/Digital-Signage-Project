import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("signage", "0015_simplify_hardware_qualification"),
    ]

    operations = [
        migrations.CreateModel(
            name="DeviceManagementCredential",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("token_hash", models.CharField(max_length=64, unique=True)),
                ("device", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="management_credential", to="signage.device")),
            ],
        ),
        migrations.CreateModel(
            name="DeviceCommand",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("admin_mode", "Admin mode")], max_length=32)),
                ("expires_at", models.DateTimeField()),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("device", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="commands", to="signage.device")),
                ("requested_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["created_at"]},
        ),
        migrations.AddIndex(
            model_name="devicecommand",
            index=models.Index(fields=["device", "acknowledged_at", "expires_at"], name="signage_cmd_pending_idx"),
        ),
    ]

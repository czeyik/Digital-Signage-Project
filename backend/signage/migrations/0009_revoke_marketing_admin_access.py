from django.db import migrations


def revoke_marketing_admin_access(apps, schema_editor):
    User = apps.get_model("signage", "User")
    AuditEvent = apps.get_model("signage", "AuditEvent")
    Session = apps.get_model("sessions", "Session")
    affected = User.objects.filter(role="marketing").filter(
        is_staff=True
    ) | User.objects.filter(role="marketing", is_superuser=True)
    count = affected.order_by().values("pk").distinct().count()
    User.objects.filter(role="marketing").update(is_staff=False, is_superuser=False)
    session_count, _ = Session.objects.all().delete()
    AuditEvent.objects.create(
        actor=None,
        action="user.marketing_admin_access.revoked",
        target_type="signage.user",
        target_id="data-migration",
        metadata={
            "account_count": count,
            "dashboard_sessions_revoked": session_count,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("sessions", "0001_initial"),
        ("signage", "0008_media_processing_dispatch"),
    ]

    operations = [
        migrations.RunPython(
            revoke_marketing_admin_access,
            reverse_code=migrations.RunPython.noop,
        )
    ]

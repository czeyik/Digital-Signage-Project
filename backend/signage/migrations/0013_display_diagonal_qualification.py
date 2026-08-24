from decimal import Decimal

from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone

MIN_QUALIFIED_DISPLAY_DIAGONAL_INCHES = Decimal("9.00")
MAX_QUALIFIED_DISPLAY_DIAGONAL_INCHES = Decimal("12.00")


def invalidate_unmeasured_hardware_approvals(apps, schema_editor):
    """Require fresh physical display-size evidence for every approval."""

    HardwareQualification = apps.get_model("signage", "HardwareQualification")
    invalid_approvals = HardwareQualification.objects.filter(
        approved_for_pilot=True
    ).filter(
        Q(measured_display_diagonal_inches__isnull=True)
        | Q(
            measured_display_diagonal_inches__lt=(
                MIN_QUALIFIED_DISPLAY_DIAGONAL_INCHES
            )
        )
        | Q(
            measured_display_diagonal_inches__gt=(
                MAX_QUALIFIED_DISPLAY_DIAGONAL_INCHES
            )
        )
    )
    now = timezone.now()
    # A malformed historic bulk approval may lack the timestamp normally set by
    # HardwareQualification.save(). Give it an immutable revocation marker;
    # retain genuine approval timestamps unchanged.
    invalid_approvals.filter(approved_at__isnull=True).update(
        approved_at=now,
        updated_at=now,
    )
    invalid_approvals.update(
        approved_for_pilot=False,
        updated_at=now,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("signage", "0012_backend_hardening"),
    ]

    operations = [
        migrations.AddField(
            model_name="hardwarequalification",
            name="measured_display_diagonal_inches",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text=(
                    "Physical corner-to-corner display diagonal in inches, "
                    "excluding bezel."
                ),
                max_digits=4,
                null=True,
            ),
        ),
        migrations.RunPython(
            invalidate_unmeasured_hardware_approvals,
            # A database reversal must not restore an approval without fresh
            # physical evidence; production migration recovery is forward-only.
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="hardwarequalification",
            constraint=models.CheckConstraint(
                condition=(
                    Q(approved_for_pilot=False)
                    | (
                        Q(measured_display_diagonal_inches__isnull=False)
                        & Q(
                            measured_display_diagonal_inches__gte=(
                                MIN_QUALIFIED_DISPLAY_DIAGONAL_INCHES
                            )
                        )
                        & Q(
                            measured_display_diagonal_inches__lte=(
                                MAX_QUALIFIED_DISPLAY_DIAGONAL_INCHES
                            )
                        )
                    )
                ),
                name="signage_hq_approved_display_size",
            ),
        ),
    ]

from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError

from signage.models import User


class Command(BaseCommand):
    help = "Create the initial account owner without enabling public registration."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--password", required=True)

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        if User.objects.exists():
            raise CommandError(
                "Users already exist; create later users through the owner."
            )
        validate_password(options["password"])
        user = User.objects.create_superuser(
            email=email,
            password=options["password"],
            role=User.Role.OWNER,
        )
        self.stdout.write(self.style.SUCCESS(f"Created owner {user.email}"))

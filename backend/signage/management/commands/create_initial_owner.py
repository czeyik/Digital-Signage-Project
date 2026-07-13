import getpass

from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError

from signage.models import User


class Command(BaseCommand):
    help = "Create the initial account owner without enabling public registration."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument(
            "--password",
            help=(
                "Avoid this on shared systems; omit it for a hidden interactive "
                "prompt."
            ),
        )

    def handle(self, *args, **options):
        email = options["email"].strip().lower()
        if User.objects.exists():
            raise CommandError(
                "Users already exist; create later users through the owner."
            )
        password = options.get("password")
        if not password:
            password = getpass.getpass("Temporary owner password: ")
            confirmation = getpass.getpass("Confirm temporary owner password: ")
            if password != confirmation:
                raise CommandError("Passwords did not match.")
        validate_password(password)
        user = User.objects.create_superuser(
            email=email,
            password=password,
            role=User.Role.OWNER,
        )
        self.stdout.write(self.style.SUCCESS(f"Created owner {user.email}"))

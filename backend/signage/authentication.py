from django.utils import timezone
from rest_framework import authentication, exceptions

from .models import DeviceAccessToken, token_hash


class DevicePrincipal:
    def __init__(self, device):
        self.device = device
        self.pk = device.pk

    @property
    def is_authenticated(self):
        return True


class DeviceAccessTokenAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        authorization = request.headers.get("Authorization", "")
        parts = authorization.split()
        if not parts:
            return None
        if len(parts) != 2 or parts[0] != self.keyword:
            raise exceptions.AuthenticationFailed("Invalid authorization header.")
        access = (
            DeviceAccessToken.objects.select_related("credential__device")
            .filter(token_hash=token_hash(parts[1]), expires_at__gt=timezone.now())
            .first()
        )
        if not access or access.credential.revoked_at:
            raise exceptions.AuthenticationFailed("Invalid or expired device token.")
        return DevicePrincipal(access.credential.device), access

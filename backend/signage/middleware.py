from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import timezone


class SessionIdleTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = int(timezone.now().timestamp())
            last_seen = request.session.get("last_activity")
            if last_seen and now - last_seen > settings.SESSION_IDLE_TIMEOUT_SECONDS:
                logout(request)
                return redirect(settings.LOGIN_URL)
            request.session["last_activity"] = now
        return self.get_response(request)

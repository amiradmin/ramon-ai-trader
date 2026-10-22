import hmac
from django.conf import settings
from django.http import JsonResponse

class BearerTokenRequired:
    """Small explicit auth helper for a private local EA endpoint."""
    @staticmethod
    def reject_if_invalid(request):
        expected = settings.EA_BEARER_TOKEN
        if not expected:
            return JsonResponse({"ok": False, "error": "EA bridge token is not configured"}, status=503)
        supplied = request.headers.get("Authorization", "")
        if not supplied.startswith("Bearer ") or not hmac.compare_digest(supplied[7:], expected):
            return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)
        return None

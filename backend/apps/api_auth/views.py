from __future__ import annotations

from typing import Any

from rest_framework import permissions, response, status, throttling
from rest_framework.authtoken.models import Token
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.api_auth.authentication import is_token_expired
from apps.api_auth.serializers import LoginSerializer, UserSerializer


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes: list[type[Any]] = []
    # Login is the only barrier between the Internet and remote code execution on
    # the operator's machine, so brute force must be expensive. Rate comes from
    # `THROTTLE_LOGIN` and is counted per IP for anonymous callers.
    throttle_classes = [throttling.ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request: Request) -> response.Response:
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        token = issue_token(user)
        return response.Response(
            {"token": token.key, "user": UserSerializer(user).data},
            status=status.HTTP_200_OK,
        )


class MeView(APIView):
    def get(self, request: Request) -> response.Response:
        return response.Response({"user": UserSerializer(request.user).data})


class LogoutView(APIView):
    def post(self, request: Request) -> response.Response:
        auth_token = getattr(request.user, "auth_token", None)
        if auth_token is not None:
            auth_token.delete()
        return response.Response(status=status.HTTP_204_NO_CONTENT)


def issue_token(user: Any) -> Token:
    """Return a live token for the user, rotating it once it is too old.

    Reusing a still-valid token keeps other browsers signed in — recreating it on
    every login would silently sign them out. An expired one is replaced rather
    than refused, because the caller has just proven their password.
    """
    token, created = Token.objects.get_or_create(user=user)
    if created or not is_token_expired(token):
        return token

    token.delete()
    return Token.objects.create(user=user)

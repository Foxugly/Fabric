from __future__ import annotations

from typing import Any

from rest_framework import permissions, response, status
from rest_framework.authtoken.models import Token
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.api_auth.serializers import LoginSerializer, UserSerializer


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes: list[type[Any]] = []

    def post(self, request: Request) -> response.Response:
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        # Reuse the existing token: recreating it here would silently sign the
        # user out of every other browser on each login.
        token, _ = Token.objects.get_or_create(user=user)
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

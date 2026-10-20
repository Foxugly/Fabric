from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from rest_framework import serializers

from apps.api_auth.models import PushItTarget
from apps.api_auth.pushit_urls import pushit_url_error


class LoginSerializer(serializers.Serializer[dict[str, Any]]):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, trim_whitespace=False)
    #: Cookie mode only: a persistent cookie instead of a browser-session one.
    remember_me = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        request = self.context.get("request")
        user = authenticate(
            request=request,
            username=attrs["username"],
            password=attrs["password"],
        )
        if user is None or not user.is_active:
            raise serializers.ValidationError("Invalid credentials")
        attrs["user"] = user
        return attrs


class PasswordChangeSerializer(serializers.Serializer[dict[str, Any]]):
    current_password = serializers.CharField(max_length=128, trim_whitespace=False)
    new_password = serializers.CharField(max_length=128, trim_whitespace=False)


class UserSerializer(serializers.ModelSerializer[Any]):
    mfa_enabled = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = ["id", "username", "is_staff", "is_superuser", "mfa_enabled"]

    def get_mfa_enabled(self, user: Any) -> bool:
        from apps.api_auth.mfa import is_enabled

        return is_enabled(user)


class MfaTokenSerializer(serializers.Serializer[dict[str, Any]]):
    mfa_token = serializers.CharField(max_length=512, trim_whitespace=True)


class MfaCodeSerializer(MfaTokenSerializer):
    #: A 6-digit TOTP code or a recovery code (`XXXXX-XXXXX`).
    code = serializers.CharField(max_length=32, trim_whitespace=True)


class PushItTargetSerializer(serializers.ModelSerializer[PushItTarget]):
    """The owner's own target. `app_token` is returned to its owner on purpose:
    the profile editor has to be able to display and change it."""

    id = serializers.IntegerField(read_only=True)
    effective_events = serializers.SerializerMethodField()

    class Meta:
        model = PushItTarget
        fields = [
            "id",
            "name",
            "app_token",
            "base_url",
            "title",
            "enabled",
            "events",
            "effective_events",
            "is_default",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "effective_events", "created_at", "updated_at"]

    def get_effective_events(self, target: PushItTarget) -> dict[str, bool]:
        """The policy actually applied, site defaults filled in."""
        return target.event_policy()

    def validate_events(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise serializers.ValidationError("events must be a JSON object")
        unknown = set(value) - set(settings.PUSHIT_DEFAULT_EVENTS)
        if unknown:
            raise serializers.ValidationError(
                f"unknown events: {', '.join(sorted(unknown))}"
            )
        return value

    def validate_base_url(self, value: str) -> str:
        error = pushit_url_error(value)
        if error is not None:
            raise serializers.ValidationError(error)
        return value.strip().rstrip("/")

    def validate_app_token(self, value: str) -> str:
        token = value.strip()
        if not token:
            raise serializers.ValidationError("app_token is required")
        return token

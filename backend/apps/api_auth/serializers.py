from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from rest_framework import serializers

from apps.api_auth.models import PushItTarget


class LoginSerializer(serializers.Serializer[dict[str, Any]]):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=128, trim_whitespace=False)

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


class UserSerializer(serializers.ModelSerializer[Any]):
    class Meta:
        model = get_user_model()
        fields = ["id", "username", "is_staff", "is_superuser"]


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

    def validate_app_token(self, value: str) -> str:
        token = value.strip()
        if not token:
            raise serializers.ValidationError("app_token is required")
        return token

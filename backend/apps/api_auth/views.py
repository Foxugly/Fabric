from __future__ import annotations

from typing import Any

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import permissions, response, status, viewsets
from rest_framework.request import Request
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from apps.api_auth import refresh_cookie
from apps.api_auth.models import PushItTarget
from apps.api_auth.refresh_cookie import PERSIST_CLAIM, deliver_tokens
from apps.api_auth.serializers import (
    LoginSerializer,
    PasswordChangeSerializer,
    PushItTargetSerializer,
    UserSerializer,
)
from apps.api_auth.sessions import revoke_refresh_tokens
from apps.api_auth.throttles import (
    AccountDeleteRateThrottle,
    LoginRateThrottle,
    LoginUsernameRateThrottle,
    PasswordChangeRateThrottle,
    WebSocketTicketRateThrottle,
)
from apps.api_auth.tickets import issue_ticket, ticket_ttl_seconds


def _token_payload(user: Any, *, persist: bool = True) -> dict[str, Any]:
    refresh = RefreshToken.for_user(user)
    if not persist:
        refresh[PERSIST_CLAIM] = False
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
        "user": UserSerializer(user).data,
    }


def _request_data(request: Request) -> Any:
    return request.data if hasattr(request.data, "get") else {}


class IsAuthenticatedOrCookieMode(permissions.BasePermission):
    """Logout: a Bearer token, or the cookie-mode opt-in (origin checked in the view).

    Lets the SPA sign out even when its in-memory access token is gone (expired,
    tab reloaded): blacklisting the caller's own refresh cookie needs nothing else.
    """

    def has_permission(self, request: Request, view: Any) -> bool:
        if refresh_cookie.wants_cookie(request):
            return True
        return bool(request.user and request.user.is_authenticated)


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes: list[type[Any]] = []
    # Login is the only barrier between the Internet and remote code execution on
    # the operator's machine: throttled per client IP (`NUM_PROXIES = 1`, so a
    # forged X-Forwarded-For prefix changes nothing) and per username.
    throttle_classes = [LoginRateThrottle, LoginUsernameRateThrottle]

    def post(self, request: Request) -> response.Response:
        if (rejected := refresh_cookie.cookie_origin_guard(request)) is not None:
            return rejected
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        # Cookie mode defaults to a browser-session cookie: "remember me" is an
        # explicit choice. Body mode has no cookie, so the claim is moot.
        persist = bool(serializer.validated_data.get("remember_me")) or (
            not refresh_cookie.wants_cookie(request)
        )
        return deliver_tokens(
            request,
            response.Response(
                _token_payload(user, persist=persist), status=status.HTTP_200_OK
            ),
        )


class TokenRefreshView(APIView):
    """Rotate the refresh token (the presented one is blacklisted).

    Body mode: `{refresh}` in, `{access, refresh}` out. Cookie mode
    (`X-Auth-Mode: cookie`): the HttpOnly cookie is used when the body has no
    `refresh` (401 `refresh_cookie_missing` if there is none); the rotated token
    replaces the cookie and is omitted from the body.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes: list[type[Any]] = []

    def get_authenticate_header(self, request: Request) -> str:
        # Without an authenticator DRF turns a rejected token into a 403; the
        # SPA ends the session on 401/403 alike, but 401 is the honest answer.
        return 'Bearer realm="api"'

    def post(self, request: Request) -> response.Response:
        if (rejected := refresh_cookie.cookie_origin_guard(request)) is not None:
            return rejected
        token = str(_request_data(request).get("refresh") or "")
        if refresh_cookie.wants_cookie(request):
            token = token or refresh_cookie.read_refresh_cookie(request)
            if not token:
                return response.Response(
                    {
                        "code": "refresh_cookie_missing",
                        "detail": "No refresh cookie: sign in again.",
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )
        serializer = TokenRefreshSerializer(data={"refresh": token})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0]) from exc
        return deliver_tokens(
            request,
            response.Response(
                dict(serializer.validated_data), status=status.HTTP_200_OK
            ),
        )


class MeView(APIView):
    def get_throttles(self) -> list[Any]:
        if self.request.method == "DELETE":
            return [AccountDeleteRateThrottle()]
        return super().get_throttles()

    def get(self, request: Request) -> response.Response:
        return response.Response({"user": UserSerializer(request.user).data})

    def delete(self, request: Request) -> response.Response:
        """Delete your own account and everything it owns (GDPR, §3.25).

        Password-confirmed: a stolen access token alone cannot erase an
        account. Agents, their commands and outputs, conversations, approvals,
        notification targets and tickets go with the user (CASCADE); every
        refresh token is blacklisted first, and the agents' open sockets are
        closed once the deletion is committed.
        """
        if (rejected := refresh_cookie.cookie_origin_guard(request)) is not None:
            return rejected
        user: Any = request.user
        password = str(_request_data(request).get("password") or "")
        if not password or not user.check_password(password):
            return _error("password_invalid", "Password is incorrect.")

        from apps.agents.models import Agent
        from apps.agents.services import disconnect_agent_sockets

        agent_ids = list(Agent.objects.filter(owner=user).values_list("id", flat=True))
        with transaction.atomic():
            revoke_refresh_tokens(user.pk)
            user.delete()

            def close_sockets() -> None:
                for agent_id in agent_ids:
                    disconnect_agent_sockets(agent_id)

            transaction.on_commit(close_sockets)

        result = response.Response(status=status.HTTP_204_NO_CONTENT)
        refresh_cookie.clear_refresh_cookie(result)
        return result


class LogoutView(APIView):
    """Blacklist the current refresh token.

    Cookie mode (no Bearer needed): blacklists the body token or else the
    cookie, always clears the cookie, answers 204 (idempotent). Body mode
    (Bearer required): `{refresh}`.
    """

    permission_classes = [IsAuthenticatedOrCookieMode]

    def post(self, request: Request) -> response.Response:
        if (rejected := refresh_cookie.cookie_origin_guard(request)) is not None:
            return rejected
        cookie_mode = refresh_cookie.wants_cookie(request)
        token = str(_request_data(request).get("refresh") or "")
        if cookie_mode:
            token = token or refresh_cookie.read_refresh_cookie(request)

        if not token and not cookie_mode:
            return response.Response(
                {
                    "code": "refresh_token_required",
                    "detail": "Refresh token is required.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if token:
            try:
                RefreshToken(token).blacklist()  # type: ignore[arg-type]
            except TokenError:
                # Idempotent: whatever the token's state, the caller ends up
                # signed out.
                pass

        result = response.Response(status=status.HTTP_204_NO_CONTENT)
        if cookie_mode:
            refresh_cookie.clear_refresh_cookie(result)
        return result


class PasswordChangeView(APIView):
    """Change the password, sign every other session out, keep this one.

    Every refresh token issued so far is blacklisted (`sessions.py`); the fresh
    pair returned here is minted afterwards, so it is the only one left valid.
    """

    throttle_classes = [PasswordChangeRateThrottle]

    def post(self, request: Request) -> response.Response:
        if (rejected := refresh_cookie.cookie_origin_guard(request)) is not None:
            return rejected
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user: Any = request.user
        current = serializer.validated_data["current_password"]
        new = serializer.validated_data["new_password"]
        if not user.check_password(current):
            return _error(
                "current_password_invalid", "Current password is incorrect."
            )
        if new == current:
            return _error(
                "password_unchanged",
                "The new password must be different from the current one.",
            )
        try:
            validate_password(new, user=user)
        except DjangoValidationError as exc:
            return response.Response(
                {
                    "code": "password_invalid",
                    "detail": " ".join(exc.messages),
                    "errors": {"new_password": list(exc.messages)},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        persist = _cookie_session_persists(request)
        with transaction.atomic():
            user.set_password(new)
            # The pre_save receiver blacklists every outstanding refresh token.
            user.save(update_fields=["password"])
            payload = _token_payload(user, persist=persist)
        return deliver_tokens(
            request,
            response.Response(
                {"code": "password_changed", **payload}, status=status.HTTP_200_OK
            ),
        )


def _error(code: str, detail: str) -> response.Response:
    return response.Response(
        {"code": code, "detail": detail}, status=status.HTTP_400_BAD_REQUEST
    )


def _cookie_session_persists(request: Request) -> bool:
    """Keep the "remember me" choice of the cookie session across a re-mint."""
    if not refresh_cookie.wants_cookie(request):
        return True
    current = refresh_cookie.read_refresh_cookie(request)
    if not current:
        return True
    try:
        token = RefreshToken(current)  # type: ignore[arg-type]
    except TokenError:
        return True
    return token.payload.get(PERSIST_CLAIM, True) is not False


class WebSocketTicketView(APIView):
    """Issue a single-use ticket (≤ 30 s) for `/ws/v1/events/?ticket=…`."""

    throttle_classes = [WebSocketTicketRateThrottle]

    def post(self, request: Request) -> response.Response:
        return response.Response(
            {
                "ticket": issue_ticket(request.user),
                "expires_in": ticket_ttl_seconds(),
            },
            status=status.HTTP_201_CREATED,
        )


class PushItTargetViewSet(viewsets.ModelViewSet[PushItTarget]):
    """CRUD on the caller's own notification targets.

    Every query is scoped to `request.user`: a target is never shared, and an
    app token belongs to whoever created it.
    """

    serializer_class = PushItTargetSerializer

    def get_queryset(self) -> Any:
        # DRF guarantees an authenticated user here; the id keeps mypy honest about
        # the AnonymousUser branch it cannot rule out.
        return PushItTarget.objects.filter(owner_id=self.request.user.id)

    def perform_create(self, serializer: Any) -> None:
        serializer.save(owner=self.request.user)

from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.agents.views import AgentViewSet
from apps.api_auth.views import (
    LoginView,
    LogoutView,
    MeView,
    MfaRecoveryCodesView,
    MfaSetupConfirmView,
    MfaSetupView,
    MfaVerifyView,
    PasswordChangeView,
    PushItTargetViewSet,
    TokenRefreshView,
    WebSocketTicketView,
)
from apps.commands.views import CommandViewSet
from apps.conversations.views import ConversationViewSet, MessageViewSet
from config.health import health

router = DefaultRouter()
router.register("agents", AgentViewSet, basename="agent")
router.register("commands", CommandViewSet, basename="command")
router.register("conversations", ConversationViewSet, basename="conversation")
router.register(
    "notification-targets", PushItTargetViewSet, basename="notification-target"
)

urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("api/v1/auth/login/", LoginView.as_view(), name="auth-login"),
    path("api/v1/auth/mfa/setup/", MfaSetupView.as_view(), name="auth-mfa-setup"),
    path(
        "api/v1/auth/mfa/setup/confirm/",
        MfaSetupConfirmView.as_view(),
        name="auth-mfa-setup-confirm",
    ),
    path("api/v1/auth/mfa/verify/", MfaVerifyView.as_view(), name="auth-mfa-verify"),
    path(
        "api/v1/auth/mfa/recovery-codes/",
        MfaRecoveryCodesView.as_view(),
        name="auth-mfa-recovery-codes",
    ),
    path("api/v1/auth/refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("api/v1/auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path(
        "api/v1/auth/password/change/",
        PasswordChangeView.as_view(),
        name="auth-password-change",
    ),
    path(
        "api/v1/auth/ws-ticket/", WebSocketTicketView.as_view(), name="auth-ws-ticket"
    ),
    path("api/v1/auth/me/", MeView.as_view(), name="auth-me"),
    path(
        "api/v1/conversations/<uuid:conversation_pk>/messages/",
        MessageViewSet.as_view({"get": "list", "post": "create"}),
        name="conversation-messages",
    ),
    path("api/v1/", include(router.urls)),
]

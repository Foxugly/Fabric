from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.agents.views import AgentViewSet
from apps.api_auth.views import LoginView, LogoutView, MeView
from apps.commands.views import CommandViewSet
from apps.conversations.views import ConversationViewSet, MessageViewSet
from config.health import health

router = DefaultRouter()
router.register("agents", AgentViewSet, basename="agent")
router.register("commands", CommandViewSet, basename="command")
router.register("conversations", ConversationViewSet, basename="conversation")

urlpatterns = [
    path("health/", health, name="health"),
    path("admin/", admin.site.urls),
    path("api/v1/auth/login/", LoginView.as_view(), name="auth-login"),
    path("api/v1/auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("api/v1/auth/me/", MeView.as_view(), name="auth-me"),
    path(
        "api/v1/conversations/<uuid:conversation_pk>/messages/",
        MessageViewSet.as_view({"get": "list", "post": "create"}),
        name="conversation-messages",
    ),
    path("api/v1/", include(router.urls)),
]

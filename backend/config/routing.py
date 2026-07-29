from django.urls import path

from apps.agents.consumers import AgentConsumer, EventConsumer

websocket_urlpatterns = [
    path("ws/v1/agent/", AgentConsumer.as_asgi()),
    path("ws/v1/events/", EventConsumer.as_asgi()),
]

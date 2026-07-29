from django.contrib import admin

from apps.commands.models import Command, CommandEvent

admin.site.register(Command)
admin.site.register(CommandEvent)

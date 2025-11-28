from django.contrib import admin

from .models import AccessLog


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    list_display = ("ip_address", "created_at", "user_agent")
    search_fields = ("ip_address", "user_agent")
    list_filter = ("created_at",)

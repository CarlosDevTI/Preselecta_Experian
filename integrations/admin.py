from django.contrib import admin
from django.utils.html import format_html

from .models import AccessLog, ConsentOTP, PreselectaQuery


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    list_display = ("ip_address", "created_at", "user_agent")
    search_fields = ("ip_address", "user_agent")
    list_filter = ("created_at",)


@admin.register(ConsentOTP)
class ConsentOTPAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "full_name",
        "id_number",
        "phone_number",
        "status",
        "decision",
        "risk_level",
        "preselecta_query_id",
        "consent_pdf_link",
    )
    search_fields = ("full_name", "id_number", "phone_number")
    list_filter = ("status", "decision", "created_at")

    @admin.display(description="Consentimiento PDF")
    def consent_pdf_link(self, obj):
        if obj.consent_pdf:
            return format_html('<a href="{}" target="_blank">Ver PDF</a>', obj.consent_pdf.url)
        return "-"


@admin.register(PreselectaQuery)
class PreselectaQueryAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "id_number",
        "first_last_name",
        "decision",
        "risk_level",
        "status",
    )
    search_fields = ("id_number", "first_last_name", "full_name")
    list_filter = ("status", "decision", "created_at")

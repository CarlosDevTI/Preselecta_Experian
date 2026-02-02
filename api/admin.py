from django.contrib import admin
from django.utils.html import format_html

from .models import CreditReportQuery


@admin.register(CreditReportQuery)
class CreditReportQueryAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "person_id_number",
        "person_last_name",
        "status",
        "http_status",
        "pdf_link",
    )
    search_fields = ("person_id_number", "person_last_name", "request_uuid")
    list_filter = ("status", "created_at")

    @admin.display(description="PDF")
    def pdf_link(self, obj):
        if obj.pdf_file:
            return format_html('<a href="{}" target="_blank">Ver PDF</a>', obj.pdf_file.url)
        return "-"

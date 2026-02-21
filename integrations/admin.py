from django.contrib import admin
from django.utils.html import format_html

from .models import AccessLog, ConsentOTP, PreselectaQuery, UserAccessProfile, OTPChallenge, OTPAuditLog


@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    list_display = ("ip_address", "requested_by_username", "requested_by_area", "requested_by_agency", "created_at", "user_agent")
    search_fields = ("ip_address", "user_agent", "requested_by_username", "requested_by_agency")
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
        "authorized_channel",
        "authorized_otp_masked",
        "requested_by_username",
        "requested_by_area",
        "requested_by_agency",
        "preselecta_query_id",
        "consent_pdf_link",
    )
    search_fields = ("full_name", "id_number", "phone_number", "email_address", "requested_by_username", "requested_by_agency")
    list_filter = ("status", "decision", "requested_by_area", "created_at")

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
        "requested_by_username",
        "requested_by_area",
        "requested_by_agency",
        "status",
    )
    search_fields = ("id_number", "first_last_name", "full_name", "requested_by_username", "requested_by_agency")
    list_filter = ("status", "decision", "requested_by_area", "created_at")


@admin.register(UserAccessProfile)
class UserAccessProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "area",
        "agency",
        "can_choose_place",
        "can_view_rejected_history",
        "must_change_password",
        "is_active",
        "updated_at",
    )
    search_fields = ("user__username", "user__first_name", "user__last_name", "agency")
    list_filter = ("area", "is_active", "can_choose_place", "can_view_rejected_history", "must_change_password")


@admin.register(OTPChallenge)
class OTPChallengeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "transaction_uuid",
        "consent",
        "channel",
        "provider",
        "destination_masked",
        "sms_message_sid",
        "status",
        "attempts_used",
        "max_attempts",
        "validation_ip",
        "generated_at",
        "expires_at",
    )
    search_fields = (
        "transaction_uuid",
        "consent__id_number",
        "destination",
        "destination_masked",
        "sms_message_sid",
        "consent__full_name",
    )
    list_filter = ("channel", "provider", "status", "generated_at")


@admin.register(OTPAuditLog)
class OTPAuditLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "consent",
        "challenge",
        "event_type",
        "channel",
        "provider",
        "result",
    )
    search_fields = ("consent__id_number", "consent__full_name", "reason")
    list_filter = ("event_type", "channel", "provider", "created_at")

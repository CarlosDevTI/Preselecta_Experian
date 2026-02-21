import re
import uuid

from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db import models
from django.utils import timezone
from django.conf import settings


def _slug_last_name(value: str) -> str:
    if not value:
        return "SINAPELLIDO"
    cleaned = re.sub(r"[^A-Za-z0-9]+", " ", value).strip().upper()
    return cleaned.replace(" ", "_") or "SINAPELLIDO"


def _unique_path(path: str) -> str:
    if not default_storage.exists(path):
        return path
    suffix = uuid.uuid4().hex[:6].upper()
    base, ext = path.rsplit(".", 1)
    return f"{base}_{suffix}.{ext}"


def consent_upload_to(instance: "ConsentOTP", filename: str) -> str:
    issued_at = instance.verified_at or timezone.now()
    date_path = issued_at.strftime("%Y/%m/%d")
    last_name = _slug_last_name(getattr(instance, "first_last_name", ""))
    id_number = getattr(instance, "id_number", "") or "SINID"
    base = f"PRE_{id_number}_{last_name}_{issued_at.strftime('%Y%m%d')}.pdf"
    return _unique_path(f"consents/{date_path}/{base}")


class PreselectaQuery(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    id_number = models.CharField(max_length=50)
    id_type = models.CharField(max_length=10)
    first_last_name = models.CharField(max_length=200, blank=True)
    full_name = models.CharField(max_length=200, blank=True)

    request_payload = models.JSONField()
    response_payload = models.JSONField(blank=True, null=True)
    decision = models.CharField(max_length=50, blank=True)
    risk_level = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, default="SUCCESS")
    error_message = models.TextField(blank=True)
    requested_by_username = models.CharField(max_length=150, blank=True)
    requested_by_area = models.CharField(max_length=40, blank=True)
    requested_by_agency = models.CharField(max_length=120, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    forwarded_for = models.TextField(blank=True, help_text="Cabecera X-Forwarded-For completa")
    user_agent = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.id_number} ({self.decision or self.status})"


class AccessLog(models.Model):
    """Registro de cada consulta con metadatos del dispositivo."""

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    forwarded_for = models.TextField(blank=True, help_text="Cabecera X-Forwarded-For completa")
    user_agent = models.TextField(blank=True)
    consulted_id_number = models.CharField(max_length=50, blank=True)
    consulted_name = models.CharField(max_length=200, blank=True)
    requested_by_username = models.CharField(max_length=150, blank=True)
    requested_by_area = models.CharField(max_length=40, blank=True)
    requested_by_agency = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.ip_address or 'unknown'} @ {self.created_at}"


class ConsentOTP(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    phone_number = models.CharField(max_length=20)
    email_address = models.EmailField(blank=True)
    channel = models.CharField(max_length=10, default="sms")
    status = models.CharField(max_length=20, default="pending")
    verify_service_sid = models.CharField(max_length=64, blank=True)
    verification_sid = models.CharField(max_length=64, blank=True)
    verification_check_sid = models.CharField(max_length=64, blank=True)
    preselecta_query = models.ForeignKey(
        "PreselectaQuery",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consents",
    )

    full_name = models.CharField(max_length=200, blank=True)
    id_number = models.CharField(max_length=50, blank=True)
    id_type = models.CharField(max_length=10, blank=True)
    first_last_name = models.CharField(max_length=200, blank=True)
    place = models.CharField(max_length=120, blank=True)
    request_payload = models.JSONField()
    decision = models.CharField(max_length=50, blank=True)
    risk_level = models.CharField(max_length=50, blank=True)
    requested_by_username = models.CharField(max_length=150, blank=True)
    requested_by_area = models.CharField(max_length=40, blank=True)
    requested_by_agency = models.CharField(max_length=120, blank=True)
    consent_pdf = models.FileField(upload_to=consent_upload_to, blank=True, null=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    forwarded_for = models.TextField(blank=True, help_text="Cabecera X-Forwarded-For completa")
    user_agent = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    resend_count = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    authorized_channel = models.CharField(max_length=10, blank=True)
    authorized_otp_masked = models.CharField(max_length=20, blank=True)
    fallback_reason = models.TextField(blank=True)
    last_error = models.TextField(blank=True)
    admin_observation = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone_number} ({self.status})"


class OTPChallenge(models.Model):
    CHANNEL_SMS = "sms"
    CHANNEL_EMAIL = "email"
    CHANNEL_CHOICES = (
        (CHANNEL_SMS, "SMS"),
        (CHANNEL_EMAIL, "Email"),
    )

    PROVIDER_TWILIO_MESSAGING = "twilio_messaging"
    PROVIDER_TWILIO_VERIFY = "twilio_verify"
    PROVIDER_INTERNAL = "internal_email"
    PROVIDER_CHOICES = (
        (PROVIDER_TWILIO_MESSAGING, "Twilio Messaging"),
        (PROVIDER_TWILIO_VERIFY, "Twilio Verify (legacy)"),
        (PROVIDER_INTERNAL, "Internal Email OTP"),
    )

    STATUS_PENDING = "pending"
    STATUS_VERIFIED = "verified"
    STATUS_EXPIRED = "expired"
    STATUS_FAILED = "failed"
    STATUS_CANCELED = "canceled"
    STATUS_BLOCKED = "blocked"
    STATUS_FAILED_SEND = "failed_send"
    STATUS_CHOICES = (
        (STATUS_PENDING, "Pending"),
        (STATUS_VERIFIED, "Verified"),
        (STATUS_EXPIRED, "Expired"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELED, "Canceled"),
        (STATUS_BLOCKED, "Blocked"),
        (STATUS_FAILED_SEND, "Failed Send"),
    )

    consent = models.ForeignKey(
        "ConsentOTP",
        on_delete=models.CASCADE,
        related_name="otp_challenges",
    )
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    provider = models.CharField(max_length=30, choices=PROVIDER_CHOICES)
    destination = models.CharField(max_length=255)
    destination_full_encrypted = models.TextField(blank=True)
    destination_masked = models.CharField(max_length=255, blank=True)
    otp_code_encrypted = models.TextField(blank=True)
    otp_hash = models.TextField(blank=True)
    otp_masked = models.CharField(max_length=20, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    transaction_uuid = models.UUIDField(default=uuid.uuid4, editable=False, db_index=True)
    generated_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    blocked_until = models.DateTimeField(null=True, blank=True)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    attempts_used = models.PositiveSmallIntegerField(default=0)
    validation_result = models.CharField(max_length=40, blank=True)
    fallback_reason = models.TextField(blank=True)
    sms_message_sid = models.CharField(max_length=64, blank=True)
    twilio_verification_sid = models.CharField(max_length=64, blank=True)
    twilio_check_sid = models.CharField(max_length=64, blank=True)
    session_key = models.CharField(max_length=120, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    forwarded_for = models.TextField(blank=True)
    user_agent = models.TextField(blank=True)
    validation_ip = models.GenericIPAddressField(null=True, blank=True)
    validation_user_agent = models.TextField(blank=True)
    context = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"{self.consent_id} {self.channel} ({self.status})"


class OTPAuditLog(models.Model):
    EVENT_GENERATED = "generated"
    EVENT_SENT = "sent"
    EVENT_VALIDATED_OK = "validated_ok"
    EVENT_VALIDATED_FAIL = "validated_fail"
    EVENT_FALLBACK_ENABLED = "fallback_enabled"
    EVENT_FALLBACK_USED = "fallback_used"
    EVENT_INVALIDATED = "invalidated"
    EVENT_CHOICES = (
        (EVENT_GENERATED, "Generated"),
        (EVENT_SENT, "Sent"),
        (EVENT_VALIDATED_OK, "Validated OK"),
        (EVENT_VALIDATED_FAIL, "Validated Fail"),
        (EVENT_FALLBACK_ENABLED, "Fallback Enabled"),
        (EVENT_FALLBACK_USED, "Fallback Used"),
        (EVENT_INVALIDATED, "Invalidated"),
    )

    consent = models.ForeignKey(
        "ConsentOTP",
        on_delete=models.CASCADE,
        related_name="otp_audit_logs",
    )
    challenge = models.ForeignKey(
        "OTPChallenge",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
    )
    event_type = models.CharField(max_length=40, choices=EVENT_CHOICES)
    channel = models.CharField(max_length=10, blank=True)
    provider = models.CharField(max_length=30, blank=True)
    otp_hash_snapshot = models.TextField(blank=True)
    result = models.CharField(max_length=40, blank=True)
    reason = models.TextField(blank=True)
    session_key = models.CharField(max_length=120, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    forwarded_for = models.TextField(blank=True)
    user_agent = models.TextField(blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event_type} ({self.channel}) - consent {self.consent_id}"

    def save(self, *args, **kwargs):
        # Log inmutable: solo insercion, nunca actualizacion.
        if self.pk:
            raise ValidationError("OTPAuditLog es inmutable y no permite actualizaciones.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("OTPAuditLog es inmutable y no permite eliminaciones.")


class UserAccessProfile(models.Model):
    AREA_AGENCIA = "AGENCIA"
    AREA_ADMINISTRATIVO = "ADMINISTRATIVO"
    AREA_TALENTO_HUMANO = "TALENTO_HUMANO"
    AREA_CARTERA = "CARTERA"
    AREA_CHOICES = (
        (AREA_AGENCIA, "Agencia"),
        (AREA_ADMINISTRATIVO, "Administrativo"),
        (AREA_TALENTO_HUMANO, "Talento Humano"),
        (AREA_CARTERA, "Cartera"),
    )

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="access_profile",
    )
    area = models.CharField(max_length=40, choices=AREA_CHOICES, default=AREA_AGENCIA)
    agency = models.CharField(max_length=120, blank=True)
    can_choose_place = models.BooleanField(default=False)
    can_view_rejected_history = models.BooleanField(default=False)
    must_change_password = models.BooleanField(
        default=False,
        help_text="Si esta activo, el usuario debe actualizar su contrasena antes de usar el modulo.",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]

    def __str__(self):
        area = self.get_area_display()
        agency = self.agency or "Sin agencia"
        return f"{self.user.username} - {area} ({agency})"

    @property
    def allows_rejected_history(self) -> bool:
        return self.area == self.AREA_TALENTO_HUMANO or self.can_view_rejected_history

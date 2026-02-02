import re
import uuid

from django.core.files.storage import default_storage
from django.db import models
from django.utils import timezone


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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.ip_address or 'unknown'} @ {self.created_at}"


class ConsentOTP(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    phone_number = models.CharField(max_length=20)
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
    consent_pdf = models.FileField(upload_to=consent_upload_to, blank=True, null=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    forwarded_for = models.TextField(blank=True, help_text="Cabecera X-Forwarded-For completa")
    user_agent = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.phone_number} ({self.status})"

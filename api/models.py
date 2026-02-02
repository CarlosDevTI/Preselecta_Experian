import re
import uuid
from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.core.files.storage import default_storage


class CreditBureauProvider(models.TextChoices):
    DATACREDITO = "DATACREDITO", "DataCredito"


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


def credit_report_upload_to(instance: "CreditReportQuery", filename: str) -> str:
    consulted_at = instance.consulted_at or timezone.now()
    date_path = consulted_at.strftime("%Y/%m/%d")
    last_name = _slug_last_name(getattr(instance, "person_last_name", ""))
    id_number = getattr(instance, "person_id_number", "") or "SINID"
    base = f"HISTORIALPAGO_{id_number}_{last_name}_{consulted_at.strftime('%Y%m%d')}.pdf"
    return _unique_path(f"credit_reports/{date_path}/{base}")


class CreditReportQuery(models.Model):
    request_uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    provider = models.CharField(max_length=50, choices=CreditBureauProvider.choices)
    service_name = models.CharField(max_length=80, blank=True)
    operation = models.CharField(max_length=80, blank=True)

    person_id_type = models.CharField(max_length=10)
    person_id_number = models.CharField(max_length=40)
    person_last_name = models.CharField(max_length=120)

    product_id = models.CharField(max_length=20, blank=True)
    info_account_type = models.CharField(max_length=20, blank=True)
    codes_value = models.CharField(max_length=50, blank=True)
    originator_channel_name = models.CharField(max_length=120, blank=True)
    originator_channel_type = models.CharField(max_length=40, blank=True)

    requested_by = models.CharField(max_length=120, blank=True)
    requester_ip = models.GenericIPAddressField(null=True, blank=True)

    status = models.CharField(max_length=20, default="PENDING")
    http_status = models.IntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)

    soap_request_xml = models.TextField(blank=True)
    soap_response_xml = models.TextField(blank=True)

    pdf_file = models.FileField(upload_to=credit_report_upload_to, blank=True, null=True)
    pdf_sha256 = models.CharField(max_length=64, blank=True)

    consulted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["provider", "person_id_type", "person_id_number"]),
        ]

    def mark_success(self) -> None:
        self.status = "SUCCESS"
        self.consulted_at = timezone.now()

    def mark_failed(self, error_message: str = "", http_status: int | None = None, error_code: str = "") -> None:
        self.status = "FAILED"
        self.error_message = error_message or self.error_message
        if http_status is not None:
            self.http_status = http_status
        if error_code:
            self.error_code = error_code
        self.consulted_at = timezone.now()

    @classmethod
    def find_recent(cls, provider: str, person_id_type: str, person_id_number: str, within_days: int = 30):
        cutoff = timezone.now() - timedelta(days=within_days)
        return (
            cls.objects.filter(
                provider=provider,
                person_id_type=str(person_id_type),
                person_id_number=str(person_id_number),
                created_at__gte=cutoff,
                status="SUCCESS",
            )
            .order_by("-created_at")
            .first()
        )

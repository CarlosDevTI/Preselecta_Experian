from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from integrations.models import ConsentOTP
from integrations.services.consent_pdf import build_consent_data, fill_consent_pdf


ID_TYPE_LABELS = {
    "1": "CC",
    "2": "NIT",
    "3": "NIT EXTRANJERIA",
    "4": "CE",
    "5": "PASAPORTE",
    "6": "CARNE DIPLOMATICO",
}


class Command(BaseCommand):
    help = "Rebuild missing consent PDFs from saved consent data."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Max records to process (0 = all)")
        parser.add_argument("--dry-run", action="store_true", help="Only report what would be rebuilt")

    def handle(self, *args, **options):
        limit = options["limit"]
        dry_run = options["dry_run"]

        qs = ConsentOTP.objects.all().order_by("id")
        missing_ids = []
        for c in qs.iterator():
            missing = not c.consent_pdf or not c.consent_pdf.name or not c.consent_pdf.storage.exists(c.consent_pdf.name)
            if missing:
                missing_ids.append(c.id)
                if limit and len(missing_ids) >= limit:
                    break

        if not missing_ids:
            self.stdout.write(self.style.SUCCESS("No missing consent PDFs found."))
            return

        processed = 0
        rebuilt = 0
        failed = 0
        for c in ConsentOTP.objects.filter(id__in=missing_ids).order_by("id"):
            processed += 1
            if dry_run:
                self.stdout.write(f"[DRY] Missing consent PDF id={c.id}, person={c.id_number}")
                continue
            try:
                issued_at = c.verified_at or c.created_at or timezone.now()
                pdf_data = build_consent_data(
                    full_name=c.full_name or "",
                    id_number=c.id_number or "",
                    id_type=ID_TYPE_LABELS.get(str(c.id_type), str(c.id_type or "")),
                    phone_number=c.phone_number or "",
                    place=c.place or "VILLAVICENCIO",
                    issued_at=issued_at,
                    authorized_channel=c.authorized_channel or c.channel or "",
                    authorized_otp_masked=c.authorized_otp_masked or "******",
                )
                pdf_bytes = fill_consent_pdf(pdf_data)
                filename = f"consent_{c.id_number or 'SINID'}_{issued_at.strftime('%Y%m%d_%H%M%S')}.pdf"
                c.consent_pdf.save(filename, ContentFile(pdf_bytes), save=True)
                rebuilt += 1
                self.stdout.write(self.style.SUCCESS(f"Rebuilt consent PDF id={c.id}"))
            except Exception as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"id={c.id}: {exc}"))

        summary = f"Processed={processed}, rebuilt={rebuilt}, failed={failed}"
        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry-run complete. {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Done. {summary}"))

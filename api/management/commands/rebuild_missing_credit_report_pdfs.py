import hashlib

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db.models import Q

from api.models import CreditReportQuery
from api.services.datacredito_report import DatacreditoReportError, xml_to_pdf_bytes


class Command(BaseCommand):
    help = "Rebuild missing credit report PDFs from stored XML without new provider calls."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Max records to process (0 = all)")
        parser.add_argument("--dry-run", action="store_true", help="Only report what would be rebuilt")

    def handle(self, *args, **options):
        limit = options["limit"]
        dry_run = options["dry_run"]

        qs = CreditReportQuery.objects.filter(status="SUCCESS").exclude(soap_response_xml="")
        broken = []
        for q in qs.iterator():
            missing = not q.pdf_file or not q.pdf_file.name or not q.pdf_file.storage.exists(q.pdf_file.name)
            if missing:
                broken.append(q.id)
                if limit and len(broken) >= limit:
                    break

        if not broken:
            self.stdout.write(self.style.SUCCESS("No missing PDFs found."))
            return

        processed = 0
        rebuilt = 0
        failed = 0
        for q in CreditReportQuery.objects.filter(id__in=broken).order_by("id"):
            processed += 1
            if dry_run:
                self.stdout.write(f"[DRY] Missing PDF for query id={q.id}, person={q.person_id_number}")
                continue
            try:
                pdf_bytes = xml_to_pdf_bytes(q.soap_response_xml)
                q.pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
                q.pdf_file.save("historial.pdf", ContentFile(pdf_bytes), save=False)
                q.save(update_fields=["pdf_sha256", "pdf_file", "updated_at"])
                rebuilt += 1
                self.stdout.write(self.style.SUCCESS(f"Rebuilt PDF for query id={q.id}"))
            except DatacreditoReportError as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"id={q.id}: {exc}"))
            except Exception as exc:
                failed += 1
                self.stderr.write(self.style.ERROR(f"id={q.id}: unexpected error: {exc}"))

        summary = f"Processed={processed}, rebuilt={rebuilt}, failed={failed}"
        if dry_run:
            self.stdout.write(self.style.WARNING(f"Dry-run complete. {summary}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Done. {summary}"))

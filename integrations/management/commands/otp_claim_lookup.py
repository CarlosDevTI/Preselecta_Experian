from django.core.management.base import BaseCommand, CommandError

from integrations.models import OTPChallenge
from integrations.services.otp_service import OTPService


class Command(BaseCommand):
    help = "Consulta OTP validado para reclamaciones (usa transaction_uuid o consent_id)."

    def add_arguments(self, parser):
        parser.add_argument("--transaction", dest="transaction_uuid", default="", help="transaction_uuid del OTPChallenge")
        parser.add_argument("--consent-id", dest="consent_id", type=int, default=0, help="ID de ConsentOTP")

    def handle(self, *args, **options):
        tx = (options.get("transaction_uuid") or "").strip()
        consent_id = int(options.get("consent_id") or 0)
        if not tx and not consent_id:
            raise CommandError("Debes enviar --transaction o --consent-id.")

        qs = OTPChallenge.objects.filter(status=OTPChallenge.STATUS_VERIFIED).order_by("-verified_at", "-generated_at")
        if tx:
            qs = qs.filter(transaction_uuid=tx)
        if consent_id:
            qs = qs.filter(consent_id=consent_id)

        challenge = qs.first()
        if not challenge:
            raise CommandError("No se encontro OTP validado con los filtros enviados.")

        try:
            destination = OTPService.decrypt_destination(challenge)
        except Exception:
            destination = challenge.destination_masked or challenge.destination or "N/A"

        if challenge.channel == OTPChallenge.CHANNEL_SMS:
            otp_value = "NO DISPONIBLE (Twilio Verify no expone codigo OTP)"
        else:
            try:
                otp_value = OTPService.decrypt_otp(challenge)
            except Exception as exc:
                raise CommandError(f"No fue posible descifrar OTP EMAIL: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("OTP de reclamacion"))
        self.stdout.write(f"consent_id: {challenge.consent_id}")
        self.stdout.write(f"transaction_uuid: {challenge.transaction_uuid}")
        self.stdout.write(f"channel: {challenge.channel}")
        self.stdout.write(f"destination: {destination}")
        self.stdout.write(f"otp: {otp_value}")
        self.stdout.write(f"verified_at: {challenge.verified_at}")

import secrets
import smtplib
import string
from dataclasses import dataclass
from datetime import timedelta
from email.mime.image import MIMEImage
from pathlib import Path

from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone

from integrations.models import ConsentOTP, OTPAuditLog, OTPChallenge
from integrations.services.otp_crypto import OTPCryptoError, decrypt_text, encrypt_text


class OTPServiceError(Exception):
    pass


@dataclass(frozen=True)
class OTPServiceConfig:
    sms_ttl_seconds: int = 600
    email_ttl_seconds: int = 600
    email_max_attempts: int = 5
    otp_digits: int = 6
    verify_max_attempts: int = 5
    temporary_block_seconds: int = 900
    rate_limit_window_seconds: int = 60
    rate_limit_ip_max: int = 20
    rate_limit_user_max: int = 30


class OTPService:
    def __init__(self, config: OTPServiceConfig):
        self.config = config

    @staticmethod
    def mask_phone(phone_number: str) -> str:
        raw = (phone_number or "").strip()
        if len(raw) <= 4:
            return "*" * len(raw)
        return f"{'*' * (len(raw) - 4)}{raw[-4:]}"

    @staticmethod
    def mask_email(email: str) -> str:
        email = (email or "").strip()
        if "@" not in email:
            return "***"
        local, domain = email.split("@", 1)
        if len(local) <= 2:
            local_masked = local[0] + "*" if local else "***"
        else:
            local_masked = local[0] + ("*" * (len(local) - 2)) + local[-1]
        return f"{local_masked}@{domain}"

    @staticmethod
    def mask_otp(code: str) -> str:
        code = (code or "").strip()
        if not code:
            return "******"
        if len(code) <= 2:
            return "*" * len(code)
        return f"{code[:2]}{'*' * max(len(code) - 2, 1)}"

    @staticmethod
    def _request_meta(request) -> dict:
        if request is None:
            return {
                "session_key": "",
                "ip_address": None,
                "forwarded_for": "",
                "user_agent": "",
                "username": "",
            }
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip = xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")
        username = ""
        user = getattr(request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            username = user.get_username()
        return {
            "session_key": request.session.session_key or "",
            "ip_address": ip,
            "forwarded_for": xff,
            "user_agent": request.META.get("HTTP_USER_AGENT", ""),
            "username": username,
        }

    @staticmethod
    def _cache_bucket(prefix: str, key: str, window_seconds: int) -> str:
        now_epoch = int(timezone.now().timestamp())
        bucket = now_epoch // max(window_seconds, 1)
        return f"otp:{prefix}:{key}:{bucket}"

    def _hit_rate_limit(self, *, prefix: str, key: str, limit: int) -> bool:
        if not key or limit <= 0:
            return False
        cache_key = self._cache_bucket(prefix, key, self.config.rate_limit_window_seconds)
        count = cache.get(cache_key)
        if count is None:
            cache.set(cache_key, 1, timeout=self.config.rate_limit_window_seconds)
            return False
        count = int(count) + 1
        cache.set(cache_key, count, timeout=self.config.rate_limit_window_seconds)
        return count > limit

    def _enforce_rate_limit(self, *, request, username: str = "") -> None:
        meta = self._request_meta(request)
        ip = meta.get("ip_address") or ""
        user_key = username or meta.get("username") or ""
        if self._hit_rate_limit(prefix="ip", key=ip, limit=self.config.rate_limit_ip_max):
            raise OTPServiceError("Bloqueo temporal por exceso de intentos desde la IP.")
        if self._hit_rate_limit(prefix="user", key=user_key, limit=self.config.rate_limit_user_max):
            raise OTPServiceError("Bloqueo temporal por exceso de intentos del usuario.")

    def _log(
        self,
        *,
        consent: ConsentOTP,
        challenge: OTPChallenge | None,
        event_type: str,
        result: str = "",
        reason: str = "",
        payload: dict | None = None,
        request=None,
    ) -> OTPAuditLog:
        meta = self._request_meta(request)
        return OTPAuditLog.objects.create(
            consent=consent,
            challenge=challenge,
            event_type=event_type,
            channel=challenge.channel if challenge else "",
            provider=challenge.provider if challenge else "",
            otp_hash_snapshot=challenge.otp_hash if challenge else "",
            result=result,
            reason=reason,
            session_key=meta["session_key"],
            ip_address=meta["ip_address"],
            forwarded_for=meta["forwarded_for"],
            user_agent=meta["user_agent"],
            payload=payload or {},
        )

    def log_event(
        self,
        *,
        consent: ConsentOTP,
        challenge: OTPChallenge | None,
        event_type: str,
        result: str = "",
        reason: str = "",
        payload: dict | None = None,
        request=None,
    ) -> OTPAuditLog:
        return self._log(
            consent=consent,
            challenge=challenge,
            event_type=event_type,
            result=result,
            reason=reason,
            payload=payload,
            request=request,
        )

    def _invalidate_pending_others(self, consent: ConsentOTP, keep_challenge_id: int) -> None:
        pending = OTPChallenge.objects.filter(consent=consent, status=OTPChallenge.STATUS_PENDING).exclude(id=keep_challenge_id)
        for challenge in pending:
            challenge.status = OTPChallenge.STATUS_CANCELED
            challenge.validation_result = "invalidated_by_success"
            challenge.save(update_fields=["status", "validation_result"])
            self._log(
                consent=consent,
                challenge=challenge,
                event_type=OTPAuditLog.EVENT_INVALIDATED,
                result="canceled",
                reason="Invalidado por validacion exitosa en otro canal",
            )

    def cancel_pending_for_new_send(self, *, consent: ConsentOTP, request=None) -> None:
        pending = OTPChallenge.objects.filter(consent=consent, status=OTPChallenge.STATUS_PENDING)
        for challenge in pending:
            challenge.status = OTPChallenge.STATUS_CANCELED
            challenge.validation_result = "replaced_by_new_send"
            challenge.save(update_fields=["status", "validation_result"])
            self._log(
                consent=consent,
                challenge=challenge,
                event_type=OTPAuditLog.EVENT_INVALIDATED,
                result="canceled",
                reason="Reemplazado por nuevo envio OTP",
                request=request,
            )

    def _generate_code(self) -> str:
        return "".join(secrets.choice(string.digits) for _ in range(max(4, self.config.otp_digits)))

    def create_sms_verify_challenge(
        self,
        *,
        consent: ConsentOTP,
        phone_number: str,
        verification_sid: str,
        request=None,
        payload: dict | None = None,
    ) -> OTPChallenge:
        self._enforce_rate_limit(request=request, username=consent.requested_by_username)
        now = timezone.now()
        meta = self._request_meta(request)
        encrypted_destination = ""
        try:
            encrypted_destination = encrypt_text(phone_number)
        except OTPCryptoError:
            # La trazabilidad sigue disponible con destino enmascarado y en ConsentOTP.
            encrypted_destination = ""

        challenge = OTPChallenge.objects.create(
            consent=consent,
            channel=OTPChallenge.CHANNEL_SMS,
            provider=OTPChallenge.PROVIDER_TWILIO_VERIFY,
            destination=self.mask_phone(phone_number),
            destination_full_encrypted=encrypted_destination,
            destination_masked=self.mask_phone(phone_number),
            otp_code_encrypted="",
            otp_hash="",
            otp_masked="******",
            status=OTPChallenge.STATUS_PENDING,
            expires_at=now + timedelta(seconds=self.config.sms_ttl_seconds),
            max_attempts=max(1, self.config.verify_max_attempts),
            attempts_used=0,
            fallback_reason="",
            twilio_verification_sid=verification_sid or "",
            session_key=meta["session_key"],
            ip_address=meta["ip_address"],
            forwarded_for=meta["forwarded_for"],
            user_agent=meta["user_agent"],
            context=payload or {},
        )
        self._log(
            consent=consent,
            challenge=challenge,
            event_type=OTPAuditLog.EVENT_GENERATED,
            result="ok",
            payload=payload,
            request=request,
        )
        self._log(
            consent=consent,
            challenge=challenge,
            event_type=OTPAuditLog.EVENT_SENT,
            result="ok",
            payload={"verification_sid": verification_sid or ""},
            request=request,
        )
        return challenge

    def create_email_challenge(
        self,
        *,
        consent: ConsentOTP,
        email_address: str,
        subject: str,
        from_email: str,
        logo_url: str,
        consentimiento_url: str,
        request=None,
        fallback_reason: str = "",
        payload: dict | None = None,
    ) -> OTPChallenge:
        self._enforce_rate_limit(request=request, username=consent.requested_by_username)
        now = timezone.now()
        meta = self._request_meta(request)
        code = self._generate_code()
        logo_cid = "congente-logo"

        try:
            encrypted_otp = encrypt_text(code)
            encrypted_destination = encrypt_text(email_address)
        except OTPCryptoError as exc:
            raise OTPServiceError(str(exc)) from exc

        challenge = OTPChallenge.objects.create(
            consent=consent,
            channel=OTPChallenge.CHANNEL_EMAIL,
            provider=OTPChallenge.PROVIDER_INTERNAL,
            destination=self.mask_email(email_address),
            destination_full_encrypted=encrypted_destination,
            destination_masked=self.mask_email(email_address),
            otp_code_encrypted=encrypted_otp,
            otp_hash=make_password(code),
            otp_masked=self.mask_otp(code),
            status=OTPChallenge.STATUS_PENDING,
            expires_at=now + timedelta(seconds=self.config.email_ttl_seconds),
            max_attempts=max(1, self.config.email_max_attempts),
            attempts_used=0,
            fallback_reason=fallback_reason,
            session_key=meta["session_key"],
            ip_address=meta["ip_address"],
            forwarded_for=meta["forwarded_for"],
            user_agent=meta["user_agent"],
            context=payload or {},
        )

        self._log(
            consent=consent,
            challenge=challenge,
            event_type=OTPAuditLog.EVENT_GENERATED,
            result="ok",
            payload=payload,
            request=request,
        )

        html_body = render_to_string(
            "integrations/email_otp.html",
            {
                "logo_url": logo_url,
                "logo_cid": logo_cid,
                "nombre_asociado": consent.full_name,
                "otp_code": code,
                "vigencia_minutos": max(1, int(self.config.email_ttl_seconds / 60)),
                "consentimiento_url": consentimiento_url,
            },
        )
        msg = EmailMultiAlternatives(
            subject=subject,
            body="Codigo OTP de Congente",
            from_email=from_email,
            to=[email_address],
        )
        msg.attach_alternative(html_body, "text/html")

        logo_file = Path(getattr(settings, "BASE_DIR")) / "static" / "img" / "LogoHD.png"
        if logo_file.exists():
            try:
                image = MIMEImage(logo_file.read_bytes())
                image.add_header("Content-ID", f"<{logo_cid}>")
                image.add_header("Content-Disposition", "inline", filename=logo_file.name)
                msg.attach(image)
            except Exception:
                # Si falla adjunto inline, el template cae al logo_url externo.
                pass

        try:
            msg.send(fail_silently=False)
        except Exception as exc:
            error_msg = str(exc)
            if isinstance(exc, smtplib.SMTPAuthenticationError) or "Username and Password not accepted" in error_msg:
                error_msg = (
                    "Credenciales SMTP invalidas. Verifica EMAIL_HOST_USER y EMAIL_HOST_PASSWORD "
                    "(en Gmail usa App Password de 16 caracteres, sin espacios)."
                )
            challenge.status = OTPChallenge.STATUS_FAILED_SEND
            challenge.validation_result = "email_send_error"
            challenge.last_error = error_msg
            challenge.save(update_fields=["status", "validation_result", "last_error"])
            self._log(
                consent=consent,
                challenge=challenge,
                event_type=OTPAuditLog.EVENT_VALIDATED_FAIL,
                result="email_send_error",
                reason=error_msg,
                request=request,
            )
            raise OTPServiceError(f"No se pudo enviar OTP por email: {error_msg}") from exc

        self._log(consent=consent, challenge=challenge, event_type=OTPAuditLog.EVENT_SENT, result="ok", request=request)
        return challenge

    def verify_email_challenge(self, *, challenge: OTPChallenge, otp_code: str, request=None) -> tuple[bool, str, OTPChallenge]:
        now = timezone.now()
        self._enforce_rate_limit(request=request, username=challenge.consent.requested_by_username)
        meta = self._request_meta(request)

        if challenge.channel != OTPChallenge.CHANNEL_EMAIL or challenge.provider != OTPChallenge.PROVIDER_INTERNAL:
            return False, "El OTP activo no corresponde a EMAIL.", challenge

        if challenge.status not in {OTPChallenge.STATUS_PENDING, OTPChallenge.STATUS_FAILED}:
            return False, "El OTP ya no esta disponible.", challenge

        if challenge.blocked_until and now < challenge.blocked_until:
            challenge.status = OTPChallenge.STATUS_BLOCKED
            challenge.validation_result = "blocked_temp"
            challenge.validation_ip = meta["ip_address"]
            challenge.validation_user_agent = meta["user_agent"]
            challenge.save(update_fields=["status", "validation_result", "validation_ip", "validation_user_agent"])
            return False, "OTP bloqueado temporalmente. Intenta mas tarde.", challenge

        if now >= challenge.expires_at:
            challenge.status = OTPChallenge.STATUS_EXPIRED
            challenge.validation_result = "expired"
            challenge.last_error = "OTP expirado"
            challenge.validation_ip = meta["ip_address"]
            challenge.validation_user_agent = meta["user_agent"]
            challenge.save(update_fields=["status", "validation_result", "last_error", "validation_ip", "validation_user_agent"])
            self._log(
                consent=challenge.consent,
                challenge=challenge,
                event_type=OTPAuditLog.EVENT_VALIDATED_FAIL,
                result="expired",
                reason="OTP expirado",
                request=request,
            )
            return False, "El OTP por EMAIL expiro. Solicita un nuevo envio.", challenge

        challenge.attempts_used += 1
        challenge.validation_ip = meta["ip_address"]
        challenge.validation_user_agent = meta["user_agent"]

        if check_password(otp_code, challenge.otp_hash):
            challenge.status = OTPChallenge.STATUS_VERIFIED
            challenge.validation_result = "approved"
            challenge.verified_at = now
            challenge.save(
                update_fields=[
                    "attempts_used",
                    "status",
                    "validation_result",
                    "verified_at",
                    "validation_ip",
                    "validation_user_agent",
                ]
            )
            self._log(
                consent=challenge.consent,
                challenge=challenge,
                event_type=OTPAuditLog.EVENT_VALIDATED_OK,
                result="approved",
                request=request,
            )
            self._invalidate_pending_others(challenge.consent, challenge.id)
            return True, "", challenge

        if challenge.attempts_used >= challenge.max_attempts:
            challenge.status = OTPChallenge.STATUS_BLOCKED
            challenge.validation_result = "max_attempts_reached"
            challenge.blocked_until = now + timedelta(seconds=self.config.temporary_block_seconds)
            challenge.last_error = "Maximo de intentos excedido"
            challenge.save(
                update_fields=[
                    "attempts_used",
                    "status",
                    "validation_result",
                    "blocked_until",
                    "last_error",
                    "validation_ip",
                    "validation_user_agent",
                ]
            )
            self._log(
                consent=challenge.consent,
                challenge=challenge,
                event_type=OTPAuditLog.EVENT_VALIDATED_FAIL,
                result="max_attempts_reached",
                reason="Maximo de intentos excedido",
                request=request,
            )
            return False, "OTP bloqueado por maximo de intentos.", challenge

        challenge.status = OTPChallenge.STATUS_FAILED
        challenge.validation_result = "invalid_code"
        challenge.last_error = "Codigo OTP invalido"
        challenge.save(
            update_fields=[
                "attempts_used",
                "status",
                "validation_result",
                "last_error",
                "validation_ip",
                "validation_user_agent",
            ]
        )
        self._log(
            consent=challenge.consent,
            challenge=challenge,
            event_type=OTPAuditLog.EVENT_VALIDATED_FAIL,
            result="invalid_code",
            reason="Codigo OTP invalido",
            request=request,
        )
        return False, "Codigo OTP invalido.", challenge

    @staticmethod
    def decrypt_otp(challenge: OTPChallenge) -> str:
        if not challenge.otp_code_encrypted:
            raise OTPServiceError("OTP completo no disponible para este canal.")
        return decrypt_text(challenge.otp_code_encrypted)

    @staticmethod
    def decrypt_destination(challenge: OTPChallenge) -> str:
        if not challenge.destination_full_encrypted:
            raise OTPServiceError("Destino completo no disponible para este registro.")
        return decrypt_text(challenge.destination_full_encrypted)

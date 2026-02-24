from django.core.files.base import ContentFile
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.conf import settings
from django.core.paginator import Paginator
from django.shortcuts import render, redirect
from django.views import View
from django.urls import reverse
from django.utils import timezone
from django.http import HttpResponse
from django.utils import timezone as dj_timezone
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
import json
import logging
import os
import requests

from .models import (
    AccessLog,
    ConsentOTP,
    OTPAuditLog,
    OTPChallenge,
    PreselectaAttemptException,
    PreselectaQuery,
    UserAccessProfile,
)
from .forms import PreselectaAuthenticationForm, PreselectaPasswordChangeForm
from .services.consent_pdf import build_consent_data, fill_consent_pdf
from .services.otp_service import OTPService, OTPServiceConfig, OTPServiceError
from .services.twilio_verify import TwilioVerifyClient
from api.models import CreditBureauProvider, CreditReportQuery
from api.services.datacredito_report import DatacreditoReportError, xml_to_pdf_bytes
from api.services.datacredito_soap import DatacreditoSoapClient, DatacreditoSoapError


logger = logging.getLogger(__name__)

DOCUMENT_TYPE_LABELS = {
    "1": "CC",
    "2": "NIT",
    "3": "NIT EXTRANJERIA",
    "4": "CE",
    "5": "PASAPORTE",
    "6": "CARNE DIPLOMATICO",
}


class PreselectaLoginView(View):
    template_name = "integrations/login.html"

    @staticmethod
    def _style_form(form: PreselectaAuthenticationForm) -> PreselectaAuthenticationForm:
        form.fields["username"].widget.attrs.update(
            {"class": "form-control", "placeholder": "Usuario"}
        )
        form.fields["password"].widget.attrs.update(
            {"class": "form-control", "placeholder": "Contrasena"}
        )
        return form

    @staticmethod
    def _get_next_url(request):
        next_url = (
            request.POST.get("next")
            or request.GET.get("next")
            or reverse("integrations:consulta")
        )
        if not next_url.startswith("/"):
            return reverse("integrations:consulta")
        return next_url

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            profile = UserAccessProfile.objects.filter(user=request.user, is_active=True).first()
            if profile:
                if profile.must_change_password:
                    next_url = self._get_next_url(request)
                    return redirect(f"{reverse('integrations:change_password')}?next={next_url}")
                return redirect("integrations:consulta")
        form = self._style_form(PreselectaAuthenticationForm(request=request))
        return render(request, self.template_name, {"form": form, "next": self._get_next_url(request)})

    def post(self, request, *args, **kwargs):
        form = self._style_form(PreselectaAuthenticationForm(request=request, data=request.POST))
        next_url = self._get_next_url(request)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "next": next_url})

        user = form.get_user()
        profile = UserAccessProfile.objects.filter(user=user, is_active=True).first()
        if not profile:
            messages.error(
                request,
                "Tu usuario no tiene perfil de acceso activo para Preselecta. Contacta a TI.",
            )
            return render(request, self.template_name, {"form": form, "next": next_url})

        auth_login(request, user)
        if profile.must_change_password:
            return redirect(f"{reverse('integrations:change_password')}?next={next_url}")
        return redirect(next_url)


class PreselectaLogoutView(View):
    def post(self, request, *args, **kwargs):
        auth_logout(request)
        return redirect("integrations:login")

    def get(self, request, *args, **kwargs):
        auth_logout(request)
        return redirect("integrations:login")


class PreselectaChangePasswordView(View):
    template_name = "integrations/change_password.html"

    @staticmethod
    def _get_next_url(request):
        next_url = (
            request.POST.get("next")
            or request.GET.get("next")
            or reverse("integrations:consulta")
        )
        if not next_url.startswith("/"):
            return reverse("integrations:consulta")
        return next_url

    @staticmethod
    def _style_form(form: PreselectaPasswordChangeForm) -> PreselectaPasswordChangeForm:
        for name in ("old_password", "new_password1", "new_password2"):
            form.fields[name].widget.attrs.update({"class": "form-control"})
        return form

    def get(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            login_url = reverse("integrations:login")
            return redirect(f"{login_url}?next={self._get_next_url(request)}")

        profile = UserAccessProfile.objects.filter(user=request.user, is_active=True).first()
        if not profile:
            auth_logout(request)
            messages.error(request, "Tu usuario no tiene perfil de acceso activo para Preselecta.")
            return redirect("integrations:login")

        if not profile.must_change_password:
            return redirect(self._get_next_url(request))

        form = self._style_form(PreselectaPasswordChangeForm(request.user))
        return render(request, self.template_name, {"form": form, "next": self._get_next_url(request)})

    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("integrations:login")

        profile = UserAccessProfile.objects.filter(user=request.user, is_active=True).first()
        if not profile:
            auth_logout(request)
            messages.error(request, "Tu usuario no tiene perfil de acceso activo para Preselecta.")
            return redirect("integrations:login")

        next_url = self._get_next_url(request)
        form = self._style_form(PreselectaPasswordChangeForm(request.user, data=request.POST))
        if not form.is_valid():
            return render(request, self.template_name, {"form": form, "next": next_url})

        user = form.save()
        update_session_auth_hash(request, user)
        if profile.must_change_password:
            profile.must_change_password = False
            profile.save(update_fields=["must_change_password", "updated_at"])

        messages.success(request, "Contrasena actualizada correctamente.")
        return redirect(next_url)


class PreselectaSecureMixin:
    @staticmethod
    def _ensure_profile(request):
        if not request.user.is_authenticated:
            return None
        profile = UserAccessProfile.objects.filter(user=request.user, is_active=True).first()
        # El flujo usa agencia del perfil de forma interna (sin seleccion manual en formulario).
        if profile and profile.agency:
            return profile
        return None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            login_url = reverse("integrations:login")
            return redirect(f"{login_url}?next={request.get_full_path()}")

        profile = self._ensure_profile(request)
        if not profile:
            messages.error(
                request,
                "Tu usuario no tiene una agencia/perfil valido para el modulo Preselecta.",
            )
            auth_logout(request)
            return redirect("integrations:login")

        if profile.must_change_password:
            change_url = reverse("integrations:change_password")
            next_url = request.get_full_path()
            return redirect(f"{change_url}?next={next_url}")

        request.preselecta_profile = profile
        return super().dispatch(request, *args, **kwargs)


class ConsultaView(PreselectaSecureMixin, View):
    template_name = 'integrations/consulta.html'  # Ruta de la plantilla
    MAX_MONTHLY_PRESELECTA_ATTEMPTS = 2
    MAX_MONTHLY_HISTORIAL_ATTEMPTS = 2
    AGENCIAS_VILLAVICENCIO = [
        "PRINCIPAL",
        "POPULAR",
        "MONTECARLO",
        "PORFIA",
        "CATAMA",
    ]
    AGENCIAS_MUNICIPIOS = [
        "ACACIAS",
        "GRANADA",
        "GUAYABETAL",
        "BARRANCA",
        "GAITAN",
        "CABUYARO",
        "VISTAHERMOSA",
    ]
    CORRESPONSALES = [
        "PUERTO LOPEZ",
        "EL CASTILLO",
        "LEJANIAS",
        "CUMARAL",
        "PUERTO RICO",
        "URIBE",
        "YOPAL",
        "VILLANUEVA",
        "MESETAS",
        "PUERTO LLERAS",
    ]

    @staticmethod
    def _extract_engine_value(response_data: dict, key: str) -> str:
        engine = response_data.get("engineResponse", []) if isinstance(response_data, dict) else []
        key_lower = key.lower()
        for item in engine:
            if str(item.get("key", "")).lower() == key_lower:
                return str(item.get("value", "")).strip()
        return ""

    @classmethod
    def _otp_allowed(cls, response_data: dict) -> tuple[bool, str, str]:
        decision = cls._extract_engine_value(response_data, "DECISION")
        risk_level = cls._extract_engine_value(response_data, "RIESGO_SCORE")
        decision_up = decision.upper()
        risk_up = risk_level.upper()
        if decision_up == "APROBADO":
            return True, decision, risk_level
        if "ZONA" in risk_up and "GRIS" in risk_up:
            return True, decision, risk_level
        if "ZONA" in decision_up and "GRIS" in decision_up:
            return True, decision, risk_level
        return False, decision, risk_level

    @staticmethod
    def _extract_full_name(response_data: dict) -> str:
        if not isinstance(response_data, dict):
            return ""
        person = response_data.get("nationalPerson") or {}
        full_name = str(person.get("fullName", "")).strip()
        if full_name:
            return full_name
        names = str(person.get("names", "")).strip()
        first_last = str(person.get("firstLastName", "")).strip()
        second_last = str(person.get("secondLastName", "")).strip()
        parts = [names, first_last, second_last]
        return " ".join([p for p in parts if p]).strip()

    @staticmethod
    def _get_client_ip(request):  #! Optenemos la ip para auditoria
        """Devuelve la IP del cliente respetando X-Forwarded-For si existe."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR")

    @staticmethod
    def _mask_phone(phone_number: str) -> str:
        phone = (phone_number or "").strip()
        if not phone:
            return ""
        if len(phone) <= 4:
            return "*" * len(phone)
        return f"{'*' * (len(phone) - 4)}{phone[-4:]}"

    @classmethod
    def _normalize_place(cls, place: str) -> str:
        cleaned = (place or "").strip()
        if not cleaned:
            return ""
        cleaned_upper = cleaned.upper()
        if cleaned_upper in cls.AGENCIAS_VILLAVICENCIO:
            return "VILLAVICENCIO"
        return cleaned

    @classmethod
    def _resolve_place(cls, place: str, profile: UserAccessProfile) -> str:
        # La agencia se determina por perfil, no por seleccion manual.
        if profile and profile.agency:
            return cls._normalize_place(profile.agency)
        return cls._normalize_place(place)

    @staticmethod
    def _clean_digits(value: str) -> str:
        return "".join(ch for ch in (value or "") if ch.isdigit())

    @classmethod
    def _compose_juridica_identifier(cls, nit: str, dv: str) -> str:
        nit_clean = cls._clean_digits(nit)
        dv_clean = cls._clean_digits(dv)
        if not nit_clean:
            return ""
        # La API de historial solo recibe "identificacion", por eso se concatena NIT+DV.
        return f"{nit_clean}{dv_clean}" if dv_clean else nit_clean

    @staticmethod
    def _normalize_phone(phone_number: str) -> str:
        phone = (phone_number or "").strip().replace(" ", "")
        if not phone:
            return ""
        if phone.startswith("+"):
            return phone
        phone = phone.lstrip("0")
        return f"+57{phone}"

    @staticmethod
    def _extract_local_phone(phone_number: str) -> str:
        phone = (phone_number or "").strip().replace(" ", "")
        if phone.startswith("+57"):
            return phone[3:]
        if phone.startswith("+"):
            return phone[1:]
        return phone.lstrip("0")

    @staticmethod
    def _otp_settings() -> tuple[int, int, int]:
        # Reglas de negocio OTP:
        # - Vigencia del OTP: 10 minutos
        # - Sin reenvio SMS
        otp_ttl = int(os.environ.get("OTP_SMS_TTL_SECONDS", os.environ.get("TWILIO_VERIFY_TTL_SECONDS", "600")))
        return otp_ttl, 0, 0

    @staticmethod
    def _otp_service() -> OTPService:
        return OTPService(
            OTPServiceConfig(
                sms_ttl_seconds=int(os.environ.get("OTP_SMS_TTL_SECONDS", "600")),
                email_ttl_seconds=int(os.environ.get("OTP_EMAIL_TTL_SECONDS", "600")),
                email_max_attempts=int(os.environ.get("OTP_EMAIL_MAX_ATTEMPTS", "5")),
                otp_digits=int(os.environ.get("OTP_EMAIL_DIGITS", "6")),
                verify_max_attempts=int(os.environ.get("OTP_MAX_ATTEMPTS", "5")),
                temporary_block_seconds=int(os.environ.get("OTP_TEMP_BLOCK_SECONDS", "900")),
                rate_limit_window_seconds=int(os.environ.get("OTP_RATE_LIMIT_WINDOW_SECONDS", "60")),
                rate_limit_ip_max=int(os.environ.get("OTP_RATE_LIMIT_IP_MAX", "20")),
                rate_limit_user_max=int(os.environ.get("OTP_RATE_LIMIT_USER_MAX", "30")),
            )
        )

    @staticmethod
    def _current_consent(consent_id) -> ConsentOTP | None:
        if not consent_id:
            return None
        return ConsentOTP.objects.filter(id=consent_id).first()

    @staticmethod
    def _active_challenge(request, consent: ConsentOTP | None) -> OTPChallenge | None:
        if not consent:
            return None
        challenge_id = request.session.get("otp_challenge_id")
        if challenge_id:
            challenge = OTPChallenge.objects.filter(id=challenge_id, consent=consent).first()
            if challenge:
                return challenge
        return (
            OTPChallenge.objects.filter(consent=consent)
            .order_by("-generated_at")
            .first()
        )

    def _otp_verify_context(
        self,
        *,
        request,
        consent: ConsentOTP | None,
        phone_number: str,
        step1_data: dict,
        step2_data: dict,
        response_data: dict,
        form_error_message: str | None,
    ) -> dict:
        otp_ttl_seconds, _, _ = self._otp_settings()
        challenge = self._active_challenge(request, consent)
        channel = challenge.channel if challenge else "sms"
        if challenge and challenge.destination_masked:
            masked_destination = challenge.destination_masked
        elif channel == OTPChallenge.CHANNEL_EMAIL:
            masked_destination = OTPService.mask_email(consent.email_address if consent else "")
        else:
            masked_destination = self._mask_phone(phone_number)

        expires_in = self._otp_seconds_left(consent, otp_ttl_seconds)
        if challenge and challenge.expires_at:
            expires_in = max(0, int((challenge.expires_at - timezone.now()).total_seconds()))
        switch_wait_seconds = 0
        can_switch_to_email = False
        if channel == OTPChallenge.CHANNEL_SMS:
            if not challenge:
                can_switch_to_email = True
            else:
                switch_wait_seconds = max(0, expires_in)
                can_switch_to_email = (
                    switch_wait_seconds <= 0
                    or challenge.status in {
                        OTPChallenge.STATUS_EXPIRED,
                        OTPChallenge.STATUS_BLOCKED,
                        OTPChallenge.STATUS_FAILED,
                        OTPChallenge.STATUS_FAILED_SEND,
                        OTPChallenge.STATUS_CANCELED,
                    }
                )

        return {
            "step": "2",
            "show_step2": True,
            "show_step3": False,
            "form_error_message": form_error_message,
            "step1_data": step1_data,
            "step2_data": step2_data,
            "response_json": response_data if response_data else None,
            "otp_allowed": True,
            "otp_stage": "verify",
            "otp_channel": channel,
            "masked_phone": masked_destination,
            "current_phone_number": (consent.phone_number if consent else phone_number),
            "otp_expires_in": expires_in,
            "otp_can_switch_to_email": can_switch_to_email,
            "otp_switch_email_wait": switch_wait_seconds,
            "fallback_email": (consent.email_address if consent else ""),
            "otp_autoshow": True,
            "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
            "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
            "corresponsales": self.CORRESPONSALES,
        }

    def _otp_send_context(
        self,
        *,
        step1_data: dict,
        step2_data: dict,
        response_data: dict,
        form_error_message: str | None,
        fallback_email: str = "",
        phone_number: str = "",
        selected_channel: str = "",
    ) -> dict:
        return {
            "step": "2",
            "show_step2": True,
            "show_step3": False,
            "form_error_message": form_error_message,
            "step1_data": step1_data,
            "step2_data": step2_data,
            "response_json": response_data if response_data else None,
            "otp_allowed": True,
            "otp_stage": "send",
            "fallback_email": fallback_email,
            "current_phone_number": phone_number,
            "otp_send_channel": selected_channel,
            "otp_autoshow": True,
            "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
            "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
            "corresponsales": self.CORRESPONSALES,
        }

    @staticmethod
    def _sms_verify_message_for_status(status: str) -> str:
        status_norm = (status or "").lower()
        if status_norm == "approved":
            return ""
        if status_norm in {"expired", "canceled"}:
            return "El OTP por SMS expiro. Puedes enviar OTP por EMAIL."
        if status_norm == "max_attempts_reached":
            return "OTP SMS bloqueado por maximo de intentos del proveedor."
        return "Codigo OTP invalido."

    @staticmethod
    def _month_start():
        now_local = timezone.localtime(timezone.now())
        return now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @classmethod
    def _preselecta_attempts_this_month(cls, id_number: str) -> int:
        if not id_number:
            return 0
        return PreselectaQuery.objects.filter(
            id_number=str(id_number),
            created_at__gte=cls._month_start(),
            # Solo contamos intentos reales al proveedor
            status__in=["SUCCESS", "FAILED"],
        ).count()

    @classmethod
    def _has_available_preselecta_exception(cls, id_number: str, id_type: str) -> bool:
        if not id_number:
            return False
        month_start = cls._month_start().date()
        return PreselectaAttemptException.objects.filter(
            id_number=str(id_number),
            month_start=month_start,
            is_active=True,
            used=False,
        ).filter(Q(id_type=str(id_type)) | Q(id_type="")).exists()

    @classmethod
    def _consume_preselecta_exception(
        cls, *, id_number: str, id_type: str, consumed_by_username: str
    ) -> PreselectaAttemptException | None:
        if not id_number:
            return None
        month_start = cls._month_start().date()
        with transaction.atomic():
            exception = (
                PreselectaAttemptException.objects.select_for_update()
                .filter(
                    id_number=str(id_number),
                    month_start=month_start,
                    is_active=True,
                    used=False,
                )
                .filter(Q(id_type=str(id_type)) | Q(id_type=""))
                .order_by("-created_at")
                .first()
            )
            if not exception:
                return None

            exception.used = True
            exception.used_at = timezone.now()
            exception.consumed_by_username = consumed_by_username or ""
            exception.save(update_fields=["used", "used_at", "consumed_by_username", "updated_at"])
            return exception

    @classmethod
    def _historial_attempts_this_month(cls, id_type: str, id_number: str) -> int:
        if not id_number:
            return 0
        return CreditReportQuery.objects.filter(
            provider=CreditBureauProvider.DATACREDITO,
            person_id_type=str(id_type),
            person_id_number=str(id_number),
            created_at__gte=cls._month_start(),
        ).count()

    @staticmethod
    def _must_skip_preselecta(profile: UserAccessProfile, id_type: str) -> tuple[bool, str]:
        # Cartera, Talento Humano y Persona Juridica no pasan por Preselecta.
        if profile.area == UserAccessProfile.AREA_CARTERA:
            return True, "Perfil cartera: consulta directa a historial."
        if profile.area == UserAccessProfile.AREA_TALENTO_HUMANO:
            return True, "Perfil Talento Humano: consulta directa a historial."
        if str(id_type) == "2":
            return True, "Persona juridica: consulta directa a historial."
        return False, ""

    @staticmethod
    def _otp_seconds_left(consent: ConsentOTP | None, ttl_seconds: int) -> int:
        if not consent or not consent.last_sent_at:
            return ttl_seconds
        elapsed = (timezone.now() - consent.last_sent_at).total_seconds()
        return max(0, ttl_seconds - int(elapsed))

    @staticmethod
    def _build_payload(id_number, id_type, first_last_name, linea_credito, tipo_asociado, medio_pago, actividad):
        return {
            "idNumber": id_number,
            "idType": id_type,
            "firstLastName": first_last_name,
            "inquiryClientId": "892000373",
            "inquiryClientType": "2",
            "inquiryUserId": "892000373",
            "inquiryUserType": "2",
            "inquiryParameters": [
                {"paramType": "STRAID", "keyvalue": {"key": "T", "value": "25674"}},
                {"paramType": "STRNAM", "keyvalue": {"key": "T", "value": "PRECREDITO_CONGENTE"}},
                {"paramType": "LINEA_CREDITO", "keyvalue": {"key": "T", "value": linea_credito}},
                {"paramType": "TIPO_ASOCIADO", "keyvalue": {"key": "T", "value": tipo_asociado}},
                {"paramType": "MEDIO_PAGO", "keyvalue": {"key": "T", "value": medio_pago}},
                {"paramType": "ACTIVIDAD", "keyvalue": {"key": "T", "value": actividad}},
            ],
        }

    def get(self, request, *args, **kwargs):
        #! Paso 1 inicial
        for key in (
            "otp_payload",
            "otp_phone",
            "otp_step1",
            "otp_step2",
            "otp_full_name",
            "otp_place",
            "otp_decision",
            "otp_risk",
            "otp_response",
            "otp_consent_id",
            "otp_challenge_id",
            "otp_channel",
            "preselecta_query_id",
            "otp_verified",
            "historial_data",
        ):
            request.session.pop(key, None)

        return render(
            request,
            self.template_name,
            {
                "step": "1",
                "show_step2": False,
                "show_step3": False,
                "step1_data": {},
                "step2_data": {},
                "phone_number": "",
                "form_error_message": None,
                "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                "corresponsales": self.CORRESPONSALES,
            },
        )

    def post(self, request, *args, **kwargs):
        step = request.POST.get("step", "1")
        form_error_message = None
        profile: UserAccessProfile = request.preselecta_profile
        requested_by_username = request.user.get_username()
        requested_by_area = profile.area
        requested_by_agency = profile.agency

        #! Datos capturados en el paso 1
        juridica_mode = str(request.POST.get("is_juridica") or "").strip() in {"1", "true", "True", "on"}
        nit_number = (request.POST.get("nit_number") or "").strip()
        nit_dv = (request.POST.get("nit_dv") or "").strip()
        razon_social = (request.POST.get("razon_social") or "").strip()
        id_number = (request.POST.get("id_number") or "").strip()
        id_type = (request.POST.get("id_type") or "").strip()
        first_last_name = (request.POST.get("first_last_name") or "").strip()
        if juridica_mode:
            id_type = "2"
            id_number = self._compose_juridica_identifier(nit_number, nit_dv)
            first_last_name = razon_social

        step1_data = {
            "idNumber": id_number,
            "idType": id_type,
            "firstLastName": first_last_name,
            "isJuridica": juridica_mode,
            "nitNumber": self._clean_digits(nit_number),
            "nitDv": self._clean_digits(nit_dv),
            "razonSocial": razon_social,
        }

        #! Paso 1: solo valida y muestra el siguiente paso, sin llamar a DATACREDITO
        if step == "1":
            if juridica_mode:
                if not step1_data["nitNumber"] or not step1_data["nitDv"] or not first_last_name:
                    form_error_message = (
                        "Para persona juridica debes ingresar NIT, digito de verificacion y razon social."
                    )
                    return render(request, self.template_name, {
                        "step": "1",
                        "show_step2": False,
                        "show_step3": False,
                        "form_error_message": form_error_message,
                        "step1_data": step1_data,
                        "step2_data": {},
                        "phone_number": "",
                        "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                        "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                        "corresponsales": self.CORRESPONSALES,
                    })
            elif not id_number or not id_type or not first_last_name:
                form_error_message = (
                    "Completa Tipo de identificacion, Numero y Primer apellido."
                )
                return render(request, self.template_name, {
                    "step": "1",
                    "show_step2": False,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": step1_data,
                    "step2_data": {},
                    "phone_number": "",
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })
            for key in (
                "otp_payload",
                "otp_phone",
                "otp_step1",
                "otp_step2",
                "otp_full_name",
                "otp_place",
                "otp_decision",
                "otp_risk",
                "otp_response",
                "otp_consent_id",
                "otp_challenge_id",
                "otp_channel",
                "preselecta_query_id",
                "otp_verified",
                "historial_data",
            ):
                request.session.pop(key, None)
            return render(request, self.template_name, {
                "step": "2",
                "show_step2": True,
                "show_step3": False,
                "form_error_message": None,
                "step1_data": step1_data,
                "step2_data": {},
                "phone_number": "",
                "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                "corresponsales": self.CORRESPONSALES,
            })

        # Paso 2: variables adicionales de la estrategia
        linea_credito = (request.POST.get("linea_credito") or "").strip()
        tipo_asociado = (request.POST.get("tipo_asociado") or "").strip()
        medio_pago = (request.POST.get("medio_pago") or "").strip()
        actividad = (request.POST.get("actividad") or "").strip()
        place = self._resolve_place("", profile)
        step2_data = {
            "linea_credito": linea_credito,
            "tipo_asociado": tipo_asociado,
            "medio_pago": medio_pago,
            "actividad": actividad,
            "place": place,
        }

        if step == "2":
            skip_preselecta, skip_reason = self._must_skip_preselecta(profile, id_type)
            preselecta_attempts = self._preselecta_attempts_this_month(id_number)
            requires_exception = (
                not skip_preselecta
                and preselecta_attempts >= self.MAX_MONTHLY_PRESELECTA_ATTEMPTS
            )

            # En flujo normal si llego al maximo mensual, se bloquea la persona.
            if requires_exception and not self._has_available_preselecta_exception(id_number, id_type):
                form_error_message = (
                    f"Esta persona ya tiene {self.MAX_MONTHLY_PRESELECTA_ATTEMPTS} intentos "
                    "de Preselecta en el mes. Consulta bloqueada. "
                    "TI puede habilitar 1 excepcion unica para este mes."
                )
                return render(request, self.template_name, {
                    "step": "2",
                    "show_step2": True,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": step1_data,
                    "step2_data": step2_data,
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })

            # Si no es flujo skip, requiere variables completas + agencia/lugar.
            if not skip_preselecta and not all(step2_data.values()):
                form_error_message = "Completa las variables y el lugar antes de consultar."
                return render(request, self.template_name, {
                    "step": "2",
                    "show_step2": True,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": step1_data,
                    "step2_data": step2_data,
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })

            # En flujo skip solo validamos que haya lugar/agencia.
            if skip_preselecta and not place:
                form_error_message = "Debes tener una agencia/lugar configurado para continuar."
                return render(request, self.template_name, {
                    "step": "2",
                    "show_step2": True,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": step1_data,
                    "step2_data": step2_data,
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })

            consumed_preselecta_exception = None
            if requires_exception:
                consumed_preselecta_exception = self._consume_preselecta_exception(
                    id_number=id_number,
                    id_type=id_type,
                    consumed_by_username=requested_by_username,
                )
                if not consumed_preselecta_exception:
                    form_error_message = (
                        "La excepcion unica para esta persona no esta disponible o ya fue usada. "
                        "Consulta bloqueada."
                    )
                    return render(request, self.template_name, {
                        "step": "2",
                        "show_step2": True,
                        "show_step3": False,
                        "form_error_message": form_error_message,
                        "step1_data": step1_data,
                        "step2_data": step2_data,
                        "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                        "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                        "corresponsales": self.CORRESPONSALES,
                    })

            response_data = {}
            response_pretty = None
            response_error = ""
            full_name = first_last_name
            decision_value = "NO_APLICA"
            risk_value = "N/A"
            otp_allowed = True if skip_preselecta else False

            payload = {
                "mode": "direct_historial" if skip_preselecta else "preselecta",
                "idNumber": id_number,
                "idType": id_type,
                "firstLastName": first_last_name,
                "linea_credito": linea_credito,
                "tipo_asociado": tipo_asociado,
                "medio_pago": medio_pago,
                "actividad": actividad,
                "place": place,
                "isJuridica": juridica_mode,
                "nitNumber": step1_data.get("nitNumber", ""),
                "nitDv": step1_data.get("nitDv", ""),
                "razonSocial": step1_data.get("razonSocial", ""),
            }

            if not skip_preselecta:
                payload = self._build_payload(
                    id_number,
                    id_type,
                    first_last_name,
                    linea_credito,
                    tipo_asociado,
                    medio_pago,
                    actividad,
                )

                api_url = request.build_absolute_uri('/api/decision/')
                try:
                    response = requests.post(api_url, json=payload)
                    response.raise_for_status()
                    response_data = response.json()
                    if response_data:
                        response_pretty = json.dumps(response_data, indent=4, ensure_ascii=False)
                except requests.exceptions.RequestException as exc:
                    response_data = {}
                    response_pretty = None
                    response_error = str(exc)
                else:
                    if isinstance(response_data, dict) and "Fault" in response_data:
                        fault = response_data.get("Fault") or {}
                        runtime = (fault.get("detail") or {}).get("runtime") or {}
                        fault_message = (
                            fault.get("faultstring")
                            or runtime.get("error-message")
                            or fault.get("faultcode")
                            or "Error en Preselecta"
                        )
                        response_error = str(fault_message)
                        response_data = {}
                        response_pretty = None

                otp_allowed, decision_value, risk_value = self._otp_allowed(response_data)
                full_name = self._extract_full_name(response_data)

            x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
            AccessLog.objects.create(
                ip_address=self._get_client_ip(request) or None,
                forwarded_for=x_forwarded_for,
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                consulted_id_number=step1_data.get("idNumber", ""),
                consulted_name=step1_data.get("firstLastName", ""),
                requested_by_username=requested_by_username,
                requested_by_area=requested_by_area,
                requested_by_agency=requested_by_agency,
            )

            preselecta_query = PreselectaQuery.objects.create(
                id_number=id_number,
                id_type=id_type,
                first_last_name=first_last_name,
                full_name=full_name,
                request_payload=payload,
                response_payload=response_data or None,
                decision=decision_value,
                risk_level=risk_value,
                # Guardamos explicitamente cuando se omite Preselecta por regla.
                status="SKIPPED" if skip_preselecta else ("SUCCESS" if response_data else "FAILED"),
                error_message=skip_reason if skip_preselecta else response_error,
                requested_by_username=requested_by_username,
                requested_by_area=requested_by_area,
                requested_by_agency=requested_by_agency,
                ip_address=self._get_client_ip(request) or None,
                forwarded_for=x_forwarded_for,
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
            request.session["preselecta_query_id"] = preselecta_query.id

            if otp_allowed:
                request.session["otp_payload"] = payload
                request.session["otp_step1"] = step1_data
                request.session["otp_step2"] = step2_data
                request.session["otp_full_name"] = full_name
                request.session["otp_place"] = place
                request.session["otp_decision"] = decision_value
                request.session["otp_risk"] = risk_value
                request.session["otp_response"] = response_data
                request.session.pop("otp_challenge_id", None)
                request.session.pop("otp_channel", None)
                request.session.pop("historial_data", None)
            else:
                request.session.pop("historial_data", None)

            datacredito_attempts = self._historial_attempts_this_month(id_type, id_number)
            if consumed_preselecta_exception:
                messages.warning(
                    request,
                    "Se aplico la excepcion unica de Preselecta para esta persona en el mes actual.",
                )
            messages.info(
                request,
                (
                    f"Límites mensuales por persona: máximo 2 intentos "
                    f"(Preselecta {min(preselecta_attempts, self.MAX_MONTHLY_PRESELECTA_ATTEMPTS)}/"
                    f"{self.MAX_MONTHLY_PRESELECTA_ATTEMPTS} | Historial {min(datacredito_attempts, self.MAX_MONTHLY_HISTORIAL_ATTEMPTS)}/"
                    f"{self.MAX_MONTHLY_HISTORIAL_ATTEMPTS})."
                ),
            )

            return render(request, self.template_name, {
                "step": "2",
                "show_step2": True,
                "show_step3": False,
                "form_error_message": None,
                "response_json": response_data if response_data else None,
                "response_pretty": response_pretty,
                "error_message": response_error or None,
                "submitted_data": payload,
                "step1_data": step1_data,
                "step2_data": step2_data,
                "otp_allowed": otp_allowed,
                "otp_stage": "send" if otp_allowed else "",
                "otp_autoshow": bool(otp_allowed),
                "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                "corresponsales": self.CORRESPONSALES,
            })

        if step == "otp_send":
            selected_channel = (request.POST.get("otp_channel") or "").strip().lower()
            if selected_channel not in {OTPChallenge.CHANNEL_SMS, OTPChallenge.CHANNEL_EMAIL}:
                selected_channel = ""

            payload = request.session.get("otp_payload")
            preselecta_query_id = request.session.get("preselecta_query_id")
            step1_data = request.session.get("otp_step1") or step1_data
            step2_data = request.session.get("otp_step2") or step2_data
            full_name = request.session.get("otp_full_name") or ""
            place = request.session.get("otp_place") or place
            decision_value = request.session.get("otp_decision", "")
            risk_value = request.session.get("otp_risk", "")
            response_data = request.session.get("otp_response", {})

            raw_phone = (request.POST.get("phone_number") or request.session.get("otp_phone") or "").strip()
            phone_number = self._normalize_phone(raw_phone)
            local_phone = self._extract_local_phone(raw_phone)
            otp_email = (request.POST.get("otp_email") or "").strip().lower()

            if not payload:
                form_error_message = "La sesion expiro. Inicia la consulta nuevamente."
                return render(request, self.template_name, {
                    "step": "1",
                    "show_step2": False,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": {},
                    "step2_data": {},
                    "phone_number": "",
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })

            consent = self._current_consent(request.session.get("otp_consent_id"))
            if not otp_email and consent and consent.email_address:
                otp_email = consent.email_address.strip().lower()
            if not raw_phone and consent and consent.phone_number:
                phone_number = consent.phone_number
                local_phone = self._extract_local_phone(phone_number)

            if not selected_channel:
                form_error_message = "Selecciona un canal para enviar el OTP."
                return render(
                    request,
                    self.template_name,
                    self._otp_send_context(
                        step1_data=step1_data,
                        step2_data=step2_data,
                        response_data=response_data,
                        form_error_message=form_error_message,
                        fallback_email=otp_email,
                        phone_number=phone_number,
                        selected_channel="",
                    ),
                )

            if selected_channel == OTPChallenge.CHANNEL_SMS:
                if not local_phone.isdigit() or len(local_phone) != 10:
                    form_error_message = "El numero de celular debe tener 10 digitos para envio SMS."
                    return render(
                        request,
                        self.template_name,
                        self._otp_send_context(
                            step1_data=step1_data,
                            step2_data=step2_data,
                            response_data=response_data,
                            form_error_message=form_error_message,
                            fallback_email=otp_email,
                            phone_number=phone_number,
                            selected_channel=selected_channel,
                        ),
                    )
            else:
                if not otp_email:
                    form_error_message = "Debes ingresar un correo para enviar OTP por EMAIL."
                    return render(
                        request,
                        self.template_name,
                        self._otp_send_context(
                            step1_data=step1_data,
                            step2_data=step2_data,
                            response_data=response_data,
                            form_error_message=form_error_message,
                            fallback_email=otp_email,
                            phone_number=phone_number,
                            selected_channel=selected_channel,
                        ),
                    )
                try:
                    validate_email(otp_email)
                except ValidationError:
                    form_error_message = "El correo para OTP no es valido."
                    return render(
                        request,
                        self.template_name,
                        self._otp_send_context(
                            step1_data=step1_data,
                            step2_data=step2_data,
                            response_data=response_data,
                            form_error_message=form_error_message,
                            fallback_email=otp_email,
                            phone_number=phone_number,
                            selected_channel=selected_channel,
                        ),
                    )

                active_sms = self._active_challenge(request, consent)
                if (
                    active_sms
                    and active_sms.channel == OTPChallenge.CHANNEL_SMS
                    and active_sms.status == OTPChallenge.STATUS_PENDING
                    and active_sms.expires_at
                    and timezone.now() < active_sms.expires_at
                ):
                    wait_seconds = max(0, int((active_sms.expires_at - timezone.now()).total_seconds()))
                    wait_minutes = wait_seconds // 60
                    wait_rem = wait_seconds % 60
                    form_error_message = (
                        "Debes esperar a que expire el OTP por SMS para habilitar EMAIL "
                        f"({wait_minutes:02d}:{wait_rem:02d})."
                    )
                    return render(
                        request,
                        self.template_name,
                        self._otp_verify_context(
                            request=request,
                            consent=consent,
                            phone_number=phone_number,
                            step1_data=step1_data,
                            step2_data=step2_data,
                            response_data=response_data,
                            form_error_message=form_error_message,
                        ),
                    )

            x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
            otp_service = self._otp_service()
            now = timezone.now()

            if not consent:
                consent = ConsentOTP.objects.create(
                    preselecta_query_id=preselecta_query_id,
                    phone_number=phone_number,
                    email_address=otp_email,
                    channel=selected_channel,
                    status="pending",
                    full_name=full_name,
                    id_number=step1_data.get("idNumber", ""),
                    id_type=step1_data.get("idType", ""),
                    first_last_name=step1_data.get("firstLastName", ""),
                    place=place,
                    request_payload=payload,
                    decision=decision_value,
                    risk_level=risk_value,
                    requested_by_username=requested_by_username,
                    requested_by_area=requested_by_area,
                    requested_by_agency=requested_by_agency,
                    ip_address=self._get_client_ip(request) or None,
                    forwarded_for=x_forwarded_for,
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    last_sent_at=now,
                    resend_count=0,
                )
            else:
                consent.preselecta_query_id = preselecta_query_id
                consent.phone_number = phone_number or consent.phone_number
                consent.email_address = otp_email or consent.email_address
                consent.channel = selected_channel
                consent.status = "pending"
                consent.last_sent_at = now
                consent.resend_count = int(consent.resend_count or 0) + 1
                consent.last_error = ""
                consent.save(
                    update_fields=[
                        "preselecta_query_id",
                        "phone_number",
                        "email_address",
                        "channel",
                        "status",
                        "last_sent_at",
                        "resend_count",
                        "last_error",
                    ]
                )

            otp_service.cancel_pending_for_new_send(consent=consent, request=request)

            try:
                if selected_channel == OTPChallenge.CHANNEL_SMS:
                    verify_client = TwilioVerifyClient()
                    template_sid = (os.environ.get("TWILIO_VERIFY_TEMPLATE_SID") or "").strip() or None
                    verification = verify_client.start_verification(
                        to_number=phone_number,
                        channel="sms",
                        template_sid=template_sid,
                        ttl_seconds=self._otp_settings()[0],
                    )
                    verification_sid = getattr(verification, "sid", "") or ""
                    challenge = otp_service.create_sms_verify_challenge(
                        consent=consent,
                        phone_number=phone_number,
                        verification_sid=verification_sid,
                        request=request,
                        payload={
                            "id_number": step1_data.get("idNumber", ""),
                            "id_type": step1_data.get("idType", ""),
                        },
                    )
                    ConsentOTP.objects.filter(id=consent.id).update(
                        channel=OTPChallenge.CHANNEL_SMS,
                        verify_service_sid=verify_client.verify_sid,
                        verification_sid=verification_sid,
                        verification_check_sid="",
                        status="pending",
                    )
                    logger.info(
                        "OTP SMS Verify enviado a %s para id_number=%s",
                        self._mask_phone(phone_number),
                        step1_data.get("idNumber", ""),
                    )
                else:
                    email_logo_url = (
                        getattr(settings, "OTP_EMAIL_LOGO_URL", "").strip()
                        or request.build_absolute_uri("/static/img/LogoHD.png")
                    )
                    email_consent_url = (
                        getattr(settings, "OTP_EMAIL_CONSENT_URL", "").strip()
                        or "https://congente.coop/images/consentimiento/autorizacion_centrales_riesgo.pdf"
                    )
                    challenge = otp_service.create_email_challenge(
                        consent=consent,
                        email_address=otp_email,
                        subject="Codigo OTP de autorizacion - Congente",
                        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@congente.co"),
                        logo_url=email_logo_url,
                        consentimiento_url=email_consent_url,
                        request=request,
                        fallback_reason="manual_channel_email",
                        payload={"id_number": step1_data.get("idNumber", "")},
                    )
                    ConsentOTP.objects.filter(id=consent.id).update(
                        channel=OTPChallenge.CHANNEL_EMAIL,
                        verification_sid="",
                        verification_check_sid="",
                        status="pending",
                        email_address=otp_email,
                    )
            except (OTPServiceError, Exception) as exc:
                logger.exception(
                    "Error enviando OTP canal=%s consent_id=%s id_number=%s",
                    selected_channel,
                    consent.id if consent else "",
                    step1_data.get("idNumber", ""),
                )
                consent.status = "error"
                consent.last_error = str(exc)
                consent.save(update_fields=["status", "last_error"])
                request.session["otp_consent_id"] = consent.id
                request.session["otp_phone"] = phone_number
                request.session["otp_channel"] = selected_channel
                request.session.pop("otp_challenge_id", None)
                form_error_message = "No se pudo enviar el codigo OTP. Intenta de nuevo."
                return render(
                    request,
                    self.template_name,
                    self._otp_send_context(
                        step1_data=step1_data,
                        step2_data=step2_data,
                        response_data=response_data,
                        form_error_message=form_error_message,
                        fallback_email=otp_email,
                        phone_number=phone_number,
                        selected_channel=selected_channel,
                    ),
                )

            consent = self._current_consent(consent.id)
            request.session["otp_consent_id"] = consent.id if consent else ""
            request.session["otp_phone"] = phone_number
            request.session["otp_challenge_id"] = challenge.id
            request.session["otp_channel"] = challenge.channel

            return render(
                request,
                self.template_name,
                self._otp_verify_context(
                    request=request,
                    consent=consent,
                    phone_number=phone_number,
                    step1_data=step1_data,
                    step2_data=step2_data,
                    response_data=response_data,
                    form_error_message=None,
                ),
            )

        if step == "otp_verify":
            otp_code = (request.POST.get("otp_code") or "").strip()
            consent_id = request.session.get("otp_consent_id")
            phone_number = request.session.get("otp_phone")
            payload = request.session.get("otp_payload")
            preselecta_query_id = request.session.get("preselecta_query_id")
            step1_data = request.session.get("otp_step1") or step1_data
            step2_data = request.session.get("otp_step2") or step2_data
            full_name = request.session.get("otp_full_name") or ""
            place = request.session.get("otp_place") or place
            decision_value = request.session.get("otp_decision", "")
            risk_value = request.session.get("otp_risk", "")
            response_data = request.session.get("otp_response", {})

            if not consent_id or not payload:
                form_error_message = "La sesion expiro. Inicia la consulta nuevamente."
                return render(request, self.template_name, {
                    "step": "1",
                    "show_step2": False,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": {},
                    "step2_data": {},
                    "phone_number": "",
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })

            consent = self._current_consent(consent_id)
            if not consent:
                form_error_message = "No se encontro la solicitud OTP. Inicia la consulta nuevamente."
                return render(request, self.template_name, {
                    "step": "1",
                    "show_step2": False,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": {},
                    "step2_data": {},
                    "phone_number": "",
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })
            phone_number = consent.phone_number or phone_number

            challenge = self._active_challenge(request, consent)
            if not challenge:
                form_error_message = "No hay un OTP activo para validar. Solicita el codigo nuevamente."
                return render(
                    request,
                    self.template_name,
                    self._otp_verify_context(
                        request=request,
                        consent=consent,
                        phone_number=phone_number,
                        step1_data=step1_data,
                        step2_data=step2_data,
                        response_data=response_data,
                        form_error_message=form_error_message,
                    ),
                )

            otp_service = self._otp_service()
            channel_label = "SMS" if challenge.channel == OTPChallenge.CHANNEL_SMS else "EMAIL"
            if not otp_code:
                form_error_message = f"Ingresa el codigo OTP enviado por {channel_label}."
                return render(
                    request,
                    self.template_name,
                    self._otp_verify_context(
                        request=request,
                        consent=consent,
                        phone_number=phone_number,
                        step1_data=step1_data,
                        step2_data=step2_data,
                        response_data=response_data,
                        form_error_message=form_error_message,
                    ),
                )

            authorized_channel = challenge.channel
            verification_check_sid = challenge.twilio_check_sid or ""
            if authorized_channel == OTPChallenge.CHANNEL_SMS:
                now = timezone.now()
                if now >= challenge.expires_at:
                    challenge.status = OTPChallenge.STATUS_EXPIRED
                    challenge.validation_result = "expired"
                    challenge.last_error = "OTP SMS expirado"
                    challenge.validation_ip = self._get_client_ip(request) or None
                    challenge.validation_user_agent = request.META.get("HTTP_USER_AGENT", "")
                    challenge.save(
                        update_fields=[
                            "status",
                            "validation_result",
                            "last_error",
                            "validation_ip",
                            "validation_user_agent",
                        ]
                    )
                    otp_service.log_event(
                        consent=consent,
                        challenge=challenge,
                        event_type=OTPAuditLog.EVENT_VALIDATED_FAIL,
                        result="expired",
                        reason="OTP SMS expirado",
                        request=request,
                    )
                    approved = False
                    message = "El OTP por SMS expiro. Puedes enviar OTP por EMAIL."
                elif challenge.attempts_used >= challenge.max_attempts:
                    challenge.status = OTPChallenge.STATUS_BLOCKED
                    challenge.validation_result = "max_attempts_reached"
                    challenge.last_error = "Maximo de intentos excedido"
                    challenge.validation_ip = self._get_client_ip(request) or None
                    challenge.validation_user_agent = request.META.get("HTTP_USER_AGENT", "")
                    challenge.save(
                        update_fields=[
                            "status",
                            "validation_result",
                            "last_error",
                            "validation_ip",
                            "validation_user_agent",
                        ]
                    )
                    approved = False
                    message = "OTP SMS bloqueado por maximo de intentos."
                else:
                    challenge.attempts_used = int(challenge.attempts_used or 0) + 1
                    challenge.validation_ip = self._get_client_ip(request) or None
                    challenge.validation_user_agent = request.META.get("HTTP_USER_AGENT", "")
                    try:
                        verify_client = TwilioVerifyClient()
                        check = verify_client.check_verification(phone_number, otp_code)
                        twilio_status = str(getattr(check, "status", "")).strip().lower()
                        verification_check_sid = getattr(check, "sid", "") or ""
                    except Exception as exc:
                        challenge.status = OTPChallenge.STATUS_FAILED
                        challenge.validation_result = "verify_error"
                        challenge.last_error = str(exc)
                        challenge.twilio_check_sid = ""
                        challenge.save(
                            update_fields=[
                                "attempts_used",
                                "status",
                                "validation_result",
                                "last_error",
                                "validation_ip",
                                "validation_user_agent",
                                "twilio_check_sid",
                            ]
                        )
                        otp_service.log_event(
                            consent=consent,
                            challenge=challenge,
                            event_type=OTPAuditLog.EVENT_VALIDATED_FAIL,
                            result="verify_error",
                            reason=str(exc),
                            request=request,
                        )
                        approved = False
                        message = "No se pudo validar OTP por SMS. Intenta nuevamente."
                    else:
                        challenge.twilio_check_sid = verification_check_sid
                        if twilio_status == "approved":
                            challenge.status = OTPChallenge.STATUS_VERIFIED
                            challenge.validation_result = "approved"
                            challenge.verified_at = now
                            challenge.last_error = ""
                            challenge.save(
                                update_fields=[
                                    "attempts_used",
                                    "status",
                                    "validation_result",
                                    "verified_at",
                                    "last_error",
                                    "validation_ip",
                                    "validation_user_agent",
                                    "twilio_check_sid",
                                ]
                            )
                            otp_service.log_event(
                                consent=consent,
                                challenge=challenge,
                                event_type=OTPAuditLog.EVENT_VALIDATED_OK,
                                result="approved",
                                payload={"check_sid": verification_check_sid},
                                request=request,
                            )
                            otp_service._invalidate_pending_others(challenge.consent, challenge.id)
                            approved = True
                            message = ""
                        else:
                            if twilio_status in {"expired", "canceled"}:
                                challenge.status = OTPChallenge.STATUS_EXPIRED
                            elif twilio_status == "max_attempts_reached" or challenge.attempts_used >= challenge.max_attempts:
                                challenge.status = OTPChallenge.STATUS_BLOCKED
                            else:
                                challenge.status = OTPChallenge.STATUS_FAILED
                            challenge.validation_result = twilio_status or "invalid_code"
                            challenge.last_error = self._sms_verify_message_for_status(twilio_status)
                            challenge.save(
                                update_fields=[
                                    "attempts_used",
                                    "status",
                                    "validation_result",
                                    "last_error",
                                    "validation_ip",
                                    "validation_user_agent",
                                    "twilio_check_sid",
                                ]
                            )
                            otp_service.log_event(
                                consent=consent,
                                challenge=challenge,
                                event_type=OTPAuditLog.EVENT_VALIDATED_FAIL,
                                result=twilio_status or "invalid_code",
                                reason=challenge.last_error,
                                payload={"check_sid": verification_check_sid},
                                request=request,
                            )
                            approved = False
                            message = self._sms_verify_message_for_status(twilio_status)
            else:
                approved, message, challenge = otp_service.verify_email_challenge(
                    challenge=challenge,
                    otp_code=otp_code,
                    request=request,
                )
            if not approved:
                ConsentOTP.objects.filter(id=consent_id).update(
                    status=challenge.status,
                    channel=authorized_channel,
                    last_error=message or challenge.last_error or "OTP no aprobado",
                )
                consent = self._current_consent(consent_id)
                return render(
                    request,
                    self.template_name,
                    self._otp_verify_context(
                        request=request,
                        consent=consent,
                        phone_number=phone_number,
                        step1_data=step1_data,
                        step2_data=step2_data,
                        response_data=response_data,
                        form_error_message=message or "Codigo OTP invalido.",
                    ),
                )

            authorized_otp_masked = challenge.otp_masked or ("******" if authorized_channel == OTPChallenge.CHANNEL_SMS else otp_service.mask_otp(otp_code))
            authorized_otp_full = otp_code if authorized_channel == OTPChallenge.CHANNEL_EMAIL else ""
            if authorized_channel == OTPChallenge.CHANNEL_EMAIL:
                try:
                    authorized_destination_full = otp_service.decrypt_destination(challenge)
                except Exception:
                    authorized_destination_full = consent.email_address or ""
            else:
                authorized_destination_full = consent.phone_number or phone_number

            issued_at = timezone.now()
            pdf_data = build_consent_data(
                full_name=full_name,
                id_number=step1_data.get("idNumber", ""),
                id_type=DOCUMENT_TYPE_LABELS.get(step1_data.get("idType", ""), step1_data.get("idType", "")),
                phone_number=consent.phone_number or phone_number,
                place=place,
                issued_at=issued_at,
                authorized_channel=authorized_channel,
                authorized_otp_masked=authorized_otp_masked,
                authorized_otp_full=authorized_otp_full,
                authorized_destination_full=authorized_destination_full,
            )
            pdf_bytes = fill_consent_pdf(pdf_data)
            filename = f"consent_{step1_data.get('idNumber','')}_{issued_at.strftime('%Y%m%d_%H%M%S')}.pdf"

            ConsentOTP.objects.filter(id=consent_id).update(
                status="approved",
                channel=authorized_channel,
                verification_sid=challenge.twilio_verification_sid if authorized_channel == OTPChallenge.CHANNEL_SMS else "",
                verification_check_sid=verification_check_sid if authorized_channel == OTPChallenge.CHANNEL_SMS else "",
                verified_at=issued_at,
                decision=decision_value,
                risk_level=risk_value,
                preselecta_query_id=preselecta_query_id,
                authorized_channel=authorized_channel,
                authorized_otp_masked=authorized_otp_masked,
                fallback_reason=challenge.fallback_reason or "",
                last_error="",
            )
            consent = ConsentOTP.objects.filter(id=consent_id).first()
            if consent:
                consent.consent_pdf.save(filename, ContentFile(pdf_bytes), save=True)

            request.session["otp_verified"] = True
            request.session["historial_data"] = {
                "person_id_number": step1_data.get("idNumber", ""),
                "person_id_type": step1_data.get("idType", ""),
                "person_last_name": step1_data.get("firstLastName", ""),
                "full_name": full_name,
            }
            messages.success(request, "OTP validado exitosamente.")

            for key in (
                "otp_payload",
                "otp_phone",
                "otp_step1",
                "otp_step2",
                "otp_full_name",
                "otp_place",
                "otp_decision",
                "otp_risk",
                "otp_response",
                "otp_consent_id",
                "otp_challenge_id",
                "otp_channel",
                "preselecta_query_id",
            ):
                request.session.pop(key, None)

            return render(request, self.template_name, {
                "step": "2",
                "show_step2": True,
                "show_step3": False,
                "form_error_message": None,
                "step1_data": step1_data,
                "step2_data": step2_data,
                "response_json": response_data if response_data else None,
                "otp_allowed": True,
                "otp_stage": "success",
                "otp_autoshow": True,
                "otp_success_message": "OTP validado exitosamente.",
                "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                "corresponsales": self.CORRESPONSALES,
            })

        form_error_message = "Paso no valido."
        return render(request, self.template_name, {
            "step": "1",
            "show_step2": False,
            "show_step3": False,
            "form_error_message": form_error_message,
            "step1_data": {},
            "step2_data": {},
            "phone_number": "",
            "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
            "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
            "corresponsales": self.CORRESPONSALES,
        })


class HistorialPagoView(PreselectaSecureMixin, View):
    template_name = "integrations/historial_pago.html"
    MAX_MONTHLY_HISTORIAL_ATTEMPTS = 2
    ID_TYPE_LABELS = {
    "1": "Cedula de Ciudadania",
    "2": "NIT",
    "3": "NIT de extranjeria",
    "4": "Cedula de extranjeria",
    "5": "Pasaporte",
    "6": "Carne diplomatico",
}

    @staticmethod
    def _can_access_history(request) -> bool:
        if not request.session.get("historial_data"):
            return False
        return bool(request.session.get("otp_verified"))

    @staticmethod
    def _month_start():
        now_local = timezone.localtime(timezone.now())
        return now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    @classmethod
    def _historial_attempts_this_month(cls, id_type: str, id_number: str) -> int:
        if not id_number:
            return 0
        return CreditReportQuery.objects.filter(
            provider=CreditBureauProvider.DATACREDITO,
            person_id_type=str(id_type),
            person_id_number=str(id_number),
            created_at__gte=cls._month_start(),
        ).count()

    def get(self, request, *args, **kwargs):
        if not self._can_access_history(request):
            messages.error(request, "Debes validar el OTP antes de consultar el historial.")
            return redirect("integrations:consulta")

        historial_data = request.session.get("historial_data") or {}
        id_type = historial_data.get("person_id_type", "")
        is_juridica = str(id_type) == "2"
        attempts = self._historial_attempts_this_month(
            historial_data.get("person_id_type", ""),
            historial_data.get("person_id_number", ""),
        )
        return render(request, self.template_name, {
            "historial_data": historial_data,
            "historial_id_type_label": self.ID_TYPE_LABELS.get(str(id_type), str(id_type)),
            "is_juridica": is_juridica,
            "historial_attempts_used": attempts,
            "historial_attempts_max": self.MAX_MONTHLY_HISTORIAL_ATTEMPTS,
        })

    def post(self, request, *args, **kwargs):
        if not self._can_access_history(request):
            messages.error(request, "Debes validar el OTP antes de consultar el historial.")
            return redirect("integrations:consulta")

        historial_data = request.session.get("historial_data") or {}
        person_id_number = historial_data.get("person_id_number", "")
        person_id_type = historial_data.get("person_id_type", "")
        person_last_name = historial_data.get("person_last_name", "")

        attempts = self._historial_attempts_this_month(person_id_type, person_id_number)
        # Bloqueo mensual por persona para controlar abuso y costo de proveedor.
        if attempts >= self.MAX_MONTHLY_HISTORIAL_ATTEMPTS:
            messages.error(
                request,
                (
                    f"Esta persona ya tiene {self.MAX_MONTHLY_HISTORIAL_ATTEMPTS} intentos "
                    "de Historial en el mes. Consulta bloqueada."
                ),
            )
            return redirect("integrations:historial_pago")

        cached = CreditReportQuery.find_recent(
            provider=CreditBureauProvider.DATACREDITO,
            person_id_type=str(person_id_type),
            person_id_number=str(person_id_number),
            within_days=30,
        )
        if cached:
            if cached.pdf_file:
                file_name = cached.pdf_file.name
                storage = cached.pdf_file.storage
                try:
                    if file_name and storage.exists(file_name):
                        cached.pdf_file.open("rb")
                        resp = HttpResponse(cached.pdf_file.read(), content_type="application/pdf")
                        resp["Content-Disposition"] = f'inline; filename="{file_name.split("/")[-1]}"'
                        return resp
                except FileNotFoundError:
                    # Si el registro existe pero el archivo fisico se perdio, regeneramos.
                    pass

                # Limpia referencia rota para evitar errores recurrentes con cache inconsistente.
                cached.pdf_file = None
                cached.save(update_fields=["pdf_file", "updated_at"])
                messages.warning(
                    request,
                    "Se encontro una consulta previa, pero el PDF no existe en disco. Se generara uno nuevo.",
                )

        try:
            client = DatacreditoSoapClient()
        except DatacreditoSoapError as exc:
            messages.error(request, f"No se pudo iniciar el servicio: {exc}")
            return redirect("integrations:historial_pago")

        is_juridica = str(person_id_type) == "2"
        q = CreditReportQuery.objects.create(
            provider=CreditBureauProvider.DATACREDITO,
            operation="consultarHC2PJ" if is_juridica else "consultarHC2",
            person_id_type=str(person_id_type),
            person_id_number=str(person_id_number),
            person_last_name=str(person_last_name),
            product_id=str(client.product_id),
            info_account_type=str(client.info_account_type),
            codes_value="",
            requested_by=(
                f"{request.user.get_username()} | "
                f"{request.preselecta_profile.area} | "
                f"{request.preselecta_profile.agency or '-'}"
            ),
            requester_ip=request.META.get("REMOTE_ADDR"),
            status="PENDING",
        )

        try:
            if is_juridica:
                result = client.consultar_hc2pj(
                    identificacion=person_id_number,
                    tipo_identificacion=person_id_type,
                    primer_apellido=person_last_name,
                    parameters=None,
                    celebrity_id="",
                )
            else:
                result = client.consultar_hc2(
                    identificacion=person_id_number,
                    tipo_identificacion=person_id_type,
                    primer_apellido=person_last_name,
                    parameters=None,
                    celebrity_id="",
                )
        except DatacreditoSoapError as exc:
            q.mark_failed(error_message=str(exc))
            q.save(update_fields=["status", "http_status", "error_code", "error_message", "consulted_at", "updated_at"])
            messages.error(request, f"No se pudo consultar historial: {exc}")
            return redirect("integrations:historial_pago")

        xml = result.get("xml")
        if not xml:
            q.mark_failed(error_message="No se pudo extraer XML del response SOAP")
            q.save(update_fields=["status", "error_message", "consulted_at", "updated_at"])
            messages.error(request, "No se pudo extraer XML del response SOAP.")
            return redirect("integrations:historial_pago")

        q.soap_request_xml = result.get("soap_request_xml", "")
        q.soap_response_xml = xml
        q.save(update_fields=["soap_request_xml", "soap_response_xml", "updated_at"])

        try:
            pdf_bytes = xml_to_pdf_bytes(xml)
        except DatacreditoReportError as exc:
            q.mark_failed(error_message=str(exc))
            q.save(update_fields=["status", "error_message", "consulted_at", "updated_at"])
            messages.error(request, f"No se pudo generar PDF: {exc}")
            return redirect("integrations:historial_pago")

        from api.views import _save_pdf
        _save_pdf(q, pdf_bytes)

        filename = f"historial_{person_id_number}.pdf"
        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{filename}"'
        return resp

class AdminAuditoriaListView(View):
    template_name = "integrations/admin_auditoria_list.html"
    ID_TYPE_LABELS = {
        "1": "Cedula de Ciudadania",
        "2": "NIT",
        "3": "NIT de extranjeria",
        "4": "Cedula de extranjeria",
        "5": "Pasaporte",
        "6": "Carne diplomatico",
    }

    @staticmethod
    def _must_change_password(user) -> bool:
        profile = getattr(user, "access_profile", None)
        return bool(profile and profile.is_active and profile.must_change_password)

    @staticmethod
    def _history_reason(consent: ConsentOTP, report_exists: bool) -> str:
        if consent.admin_observation:
            return consent.admin_observation
        if report_exists:
            return ""
        if consent.status != "approved":
            return "No se consulto historial porque OTP no fue confirmado."
        if consent.last_error:
            return consent.last_error
        return "Sin consulta de historial."

    @staticmethod
    def _authorization_summary(consent: ConsentOTP) -> str:
        challenge = (
            OTPChallenge.objects.filter(consent=consent, status=OTPChallenge.STATUS_VERIFIED)
            .order_by("-verified_at", "-generated_at")
            .first()
        )
        channel = (consent.authorized_channel or consent.channel or "").lower()
        otp_masked = consent.authorized_otp_masked or (challenge.otp_masked if challenge else "") or "******"
        if channel == OTPChallenge.CHANNEL_EMAIL:
            destination = (
                challenge.destination_masked
                if challenge and challenge.destination_masked
                else "***@***"
            )
            return f"Autorizado vía EMAIL al correo {destination} con OTP {otp_masked}"
        destination = (
            challenge.destination_masked
            if challenge and challenge.destination_masked
            else OTPService.mask_phone(consent.phone_number)
        )
        return f"Autorizado vía SMS al número {destination} con OTP {otp_masked}"

    @staticmethod
    def _can_access_auditoria(user) -> bool:
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        profile = getattr(user, "access_profile", None)
        if not profile or not profile.is_active:
            return False
        # Acceso de lectura a auditoria solo para rol administrativo.
        return profile.area == UserAccessProfile.AREA_ADMINISTRATIVO

    def get(self, request, *args, **kwargs):
        user = getattr(request, "user", None)
        if self._must_change_password(user):
            messages.warning(request, "Debes cambiar tu contrasena antes de acceder a Auditoria.")
            next_url = reverse("integrations:admin_auditoria_list")
            return redirect(f"{reverse('integrations:change_password')}?next={next_url}")
        if not self._can_access_auditoria(user):
            messages.error(request, "Acceso restringido. Inicia sesion con un usuario autorizado.")
            login_url = reverse("integrations:login")
            next_url = reverse("integrations:admin_auditoria_list")
            return redirect(f"{login_url}?next={next_url}")

        consent_qs = ConsentOTP.objects.select_related("preselecta_query").order_by("-created_at")
        paginator = Paginator(consent_qs, 25)
        page_obj = paginator.get_page(request.GET.get("page"))
        consents = list(page_obj.object_list)
        id_numbers = {c.id_number for c in consents if c.id_number}
        reports = CreditReportQuery.objects.filter(
            person_id_number__in=id_numbers, status="SUCCESS"
        ).order_by("person_id_number", "-created_at")
        report_map = {}
        for report in reports:
            if report.person_id_number not in report_map:
                report_map[report.person_id_number] = report

        rows = []
        for consent in consents:
            preselecta = consent.preselecta_query
            report = report_map.get(consent.id_number)
            consent_pdf_exists = bool(
                consent.consent_pdf
                and consent.consent_pdf.name
                and consent.consent_pdf.storage.exists(consent.consent_pdf.name)
            )
            report_pdf_exists = bool(
                report
                and report.pdf_file
                and report.pdf_file.name
                and report.pdf_file.storage.exists(report.pdf_file.name)
            )
            history_reason = self._history_reason(consent, bool(report))
            rows.append(
                {
                    "consent": consent,
                    "preselecta": preselecta,
                    "report": report,
                    "consent_pdf_exists": consent_pdf_exists,
                    "report_pdf_exists": report_pdf_exists,
                    "history_reason": history_reason,
                    "id_type_label": self.ID_TYPE_LABELS.get(str(consent.id_type), str(consent.id_type or "N/A")),
                    "authorization_summary": self._authorization_summary(consent),
                }
            )

        return render(request, self.template_name, {
            "rows": rows,
            "page_obj": page_obj,
            "id_type_labels": self.ID_TYPE_LABELS,
        })


class AdminAuditoriaDetailView(View):
    template_name = "integrations/admin_auditoria_detail.html"
    ID_TYPE_LABELS = AdminAuditoriaListView.ID_TYPE_LABELS

    @staticmethod
    def _extract_engine_value(response_data: dict, key: str) -> str:
        engine = response_data.get("engineResponse", []) if isinstance(response_data, dict) else []
        key_lower = key.lower()
        for item in engine:
            if str(item.get("key", "")).lower() == key_lower:
                return str(item.get("value", "")).strip()
        return ""

    def get(self, request, consent_id: int, *args, **kwargs):
        user = getattr(request, "user", None)
        if AdminAuditoriaListView._must_change_password(user):
            messages.warning(request, "Debes cambiar tu contrasena antes de acceder a Auditoria.")
            next_url = reverse("integrations:admin_auditoria_detail", args=[consent_id])
            return redirect(f"{reverse('integrations:change_password')}?next={next_url}")
        if not AdminAuditoriaListView._can_access_auditoria(user):
            messages.error(request, "Acceso restringido. Inicia sesion con un usuario autorizado.")
            login_url = reverse("integrations:login")
            next_url = reverse("integrations:admin_auditoria_detail", args=[consent_id])
            return redirect(f"{login_url}?next={next_url}")

        consent = ConsentOTP.objects.select_related("preselecta_query").filter(id=consent_id).first()
        if not consent:
            messages.error(request, "Registro no encontrado.")
            return redirect("integrations:admin_auditoria_list")

        return self._render_detail(request, consent)

    def post(self, request, consent_id: int, *args, **kwargs):
        user = getattr(request, "user", None)
        if AdminAuditoriaListView._must_change_password(user):
            messages.warning(request, "Debes cambiar tu contrasena antes de acceder a Auditoria.")
            next_url = reverse("integrations:admin_auditoria_detail", args=[consent_id])
            return redirect(f"{reverse('integrations:change_password')}?next={next_url}")
        if not AdminAuditoriaListView._can_access_auditoria(user):
            messages.error(request, "Acceso restringido. Inicia sesion con un usuario autorizado.")
            login_url = reverse("integrations:login")
            next_url = reverse("integrations:admin_auditoria_detail", args=[consent_id])
            return redirect(f"{login_url}?next={next_url}")
        if not user.is_superuser:
            messages.error(request, "Solo el superadministrador puede editar observaciones.")
            return redirect("integrations:admin_auditoria_detail", consent_id=consent_id)

        consent = ConsentOTP.objects.select_related("preselecta_query").filter(id=consent_id).first()
        if not consent:
            messages.error(request, "Registro no encontrado.")
            return redirect("integrations:admin_auditoria_list")

        consent.admin_observation = (request.POST.get("admin_observation") or "").strip()
        consent.save(update_fields=["admin_observation"])
        messages.success(request, "Observacion guardada correctamente.")
        return redirect("integrations:admin_auditoria_detail", consent_id=consent_id)

    def _render_detail(self, request, consent: ConsentOTP):
        report = None
        if consent.id_number:
            report = (
                CreditReportQuery.objects.filter(
                    person_id_number=consent.id_number, status="SUCCESS"
                )
                .order_by("-created_at")
                .first()
            )

        summary = {}
        if consent.preselecta_query and isinstance(consent.preselecta_query.response_payload, dict):
            resp = consent.preselecta_query.response_payload
            summary = {
                "decision": self._extract_engine_value(resp, "DECISION"),
                "risk_level": self._extract_engine_value(resp, "RIESGO_SCORE"),
                "score": (resp.get("score") or {}).get("rating", ""),
                "status": resp.get("typeResponse", "") or "SUCCESS",
                "id_number": (resp.get("nationalPerson") or {}).get("identification", {}).get("number", ""),
                "id_type": (resp.get("nationalPerson") or {}).get("identification", {}).get("type", ""),
                "first_last": (resp.get("nationalPerson") or {}).get("firstLastName", ""),
                "second_last": (resp.get("nationalPerson") or {}).get("secondLastName", ""),
                "names": (resp.get("nationalPerson") or {}).get("names", ""),
            }

        consent_pdf_exists = bool(
            consent.consent_pdf
            and consent.consent_pdf.name
            and consent.consent_pdf.storage.exists(consent.consent_pdf.name)
        )
        report_pdf_exists = bool(
            report
            and report.pdf_file
            and report.pdf_file.name
            and report.pdf_file.storage.exists(report.pdf_file.name)
        )

        history_reason = ""
        if consent.admin_observation:
            history_reason = consent.admin_observation
        elif not report:
            if consent.status != "approved":
                history_reason = "No se consulto historial porque OTP no fue confirmado."
            elif consent.last_error:
                history_reason = consent.last_error

        authorization_summary = AdminAuditoriaListView._authorization_summary(consent)

        return render(
            request,
            self.template_name,
            {
                "consent": consent,
                "preselecta": consent.preselecta_query,
                "report": report,
                "consent_pdf_exists": consent_pdf_exists,
                "report_pdf_exists": report_pdf_exists,
                "history_reason": history_reason,
                "preselecta_summary": summary,
                "authorization_summary": authorization_summary,
                "id_type_label": self.ID_TYPE_LABELS.get(str(consent.id_type), str(consent.id_type or "N/A")),
                "id_type_labels": self.ID_TYPE_LABELS,
                "can_edit_admin_observation": bool(getattr(request.user, "is_superuser", False)),
            },
        )

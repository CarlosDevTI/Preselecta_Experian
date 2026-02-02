from django.core.files.base import ContentFile
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views import View
from django.urls import reverse
from django.utils import timezone
from django.http import HttpResponse
from django.utils import timezone as dj_timezone
import json
import logging
import os
import requests

from .models import AccessLog, ConsentOTP, PreselectaQuery
from .services.consent_pdf import build_consent_data, fill_consent_pdf
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


class ConsultaView(View):
    template_name = 'integrations/consulta.html'  # Ruta de la plantilla
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

        #! Datos capturados en el paso 1
        id_number = (request.POST.get("id_number") or "").strip()
        id_type = (request.POST.get("id_type") or "").strip()
        first_last_name = (request.POST.get("first_last_name") or "").strip()
        step1_data = {
            "idNumber": id_number,
            "idType": id_type,
            "firstLastName": first_last_name,
        }

        #! Paso 1: solo valida y muestra el siguiente paso, sin llamar a DATACREDITO
        if step == "1":
            if not id_number or not id_type or not first_last_name:
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

        #? Paso 2: variables adicionales de la estrategia
        linea_credito = (request.POST.get("linea_credito") or "").strip()
        tipo_asociado = (request.POST.get("tipo_asociado") or "").strip()
        medio_pago = (request.POST.get("medio_pago") or "").strip()
        actividad = (request.POST.get("actividad") or "").strip()
        place = (request.POST.get("place") or "").strip()
        step2_data = {
            "linea_credito": linea_credito,
            "tipo_asociado": tipo_asociado,
            "medio_pago": medio_pago,
            "actividad": actividad,
            "place": place,
        }

        if step == "2":
            #? Si falta algo del paso 2, no se llama al proveedor
            if not all(step2_data.values()):
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
            response_data = {}
            response_pretty = None
            response_error = ""
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

            x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
            AccessLog.objects.create(
                ip_address=self._get_client_ip(request) or None,
                forwarded_for=x_forwarded_for,
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
                consulted_id_number=step1_data.get("idNumber", ""),
                consulted_name=step1_data.get("firstLastName", ""),
            )

            otp_allowed, decision_value, risk_value = self._otp_allowed(response_data)
            full_name = self._extract_full_name(response_data)

            preselecta_query = PreselectaQuery.objects.create(
                id_number=id_number,
                id_type=id_type,
                first_last_name=first_last_name,
                full_name=full_name,
                request_payload=payload,
                response_payload=response_data or None,
                decision=decision_value,
                risk_level=risk_value,
                status="SUCCESS" if response_data else "FAILED",
                error_message=response_error,
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

            return render(request, self.template_name, {
                "step": "2",
                "show_step2": True,
                "show_step3": False,
                "form_error_message": None,
                "response_json": response_data if response_data else None,
                "response_pretty": response_pretty,
                "error_message": None,
                "submitted_data": payload,
                "step1_data": step1_data,
                "step2_data": step2_data,
                "otp_allowed": otp_allowed,
                "otp_stage": "send" if otp_allowed else "",
                "otp_autoshow": False,
                "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                "corresponsales": self.CORRESPONSALES,
            })

        if step == "otp_send":
            raw_phone = request.POST.get("phone_number")
            phone_number = self._normalize_phone(raw_phone)
            local_phone = self._extract_local_phone(raw_phone)
            payload = request.session.get("otp_payload")
            preselecta_query_id = request.session.get("preselecta_query_id")
            step1_data = request.session.get("otp_step1") or step1_data
            step2_data = request.session.get("otp_step2") or step2_data
            full_name = request.session.get("otp_full_name") or ""
            place = request.session.get("otp_place") or place
            decision_value = request.session.get("otp_decision", "")
            risk_value = request.session.get("otp_risk", "")
            response_data = request.session.get("otp_response", {})

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

            if not local_phone.isdigit() or len(local_phone) != 10:
                form_error_message = "El numero de celular debe tener 10 digitos."
                return render(request, self.template_name, {
                    "step": "2",
                    "show_step2": True,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": step1_data,
                    "step2_data": step2_data,
                    "response_json": response_data if response_data else None,
                    "otp_allowed": True,
                    "otp_stage": "send",
                    "otp_autoshow": True,
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })

            x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
            channel = os.environ.get("TWILIO_VERIFY_CHANNEL", "sms")
            template_sid = os.environ.get("TWILIO_VERIFY_TEMPLATE_SID", "").strip()

            try:
                verify_client = TwilioVerifyClient()
                verification = verify_client.start_verification(
                    phone_number,
                    channel=channel,
                    template_sid=template_sid or None,
                )
            except Exception as e:
                ConsentOTP.objects.create(
                    phone_number=phone_number,
                    channel=channel,
                    status="error",
                    verify_service_sid=os.environ.get("TWILIO_VERIFY_SID", ""),
                    full_name=full_name,
                    id_number=step1_data.get("idNumber", ""),
                    id_type=step1_data.get("idType", ""),
                    first_last_name=step1_data.get("firstLastName", ""),
                    place=place,
                    request_payload=payload,
                    decision=decision_value,
                    risk_level=risk_value,
                    ip_address=self._get_client_ip(request) or None,
                    forwarded_for=x_forwarded_for,
                    user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    last_error=str(e),
                )
                form_error_message = "No se pudo enviar el codigo OTP. Intenta de nuevo."
                return render(request, self.template_name, {
                    "step": "2",
                    "show_step2": True,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": step1_data,
                    "step2_data": step2_data,
                    "response_json": response_data if response_data else None,
                    "otp_allowed": True,
                    "otp_stage": "send",
                    "otp_autoshow": True,
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })

            consent = ConsentOTP.objects.create(
                preselecta_query_id=preselecta_query_id,
                phone_number=phone_number,
                channel=verification.channel,
                status=verification.status,
                verify_service_sid=verify_client.verify_sid,
                verification_sid=verification.sid,
                full_name=full_name,
                id_number=step1_data.get("idNumber", ""),
                id_type=step1_data.get("idType", ""),
                first_last_name=step1_data.get("firstLastName", ""),
                place=place,
                request_payload=payload,
                decision=decision_value,
                risk_level=risk_value,
                ip_address=self._get_client_ip(request) or None,
                forwarded_for=x_forwarded_for,
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
            logger.info(
                "OTP enviado exitosamente a %s para id_number=%s",
                self._mask_phone(phone_number),
                step1_data.get("idNumber", ""),
            )

            request.session["otp_consent_id"] = consent.id
            request.session["otp_phone"] = phone_number

            return render(request, self.template_name, {
                "step": "2",
                "show_step2": True,
                "show_step3": False,
                "form_error_message": None,
                "response_json": response_data if response_data else None,
                "step1_data": step1_data,
                "step2_data": step2_data,
                "otp_allowed": True,
                "otp_stage": "verify",
                "masked_phone": self._mask_phone(phone_number),
                "otp_autoshow": True,
                "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                "corresponsales": self.CORRESPONSALES,
            })

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

            if not consent_id or not payload or not phone_number:
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

            if not otp_code:
                form_error_message = "Ingresa el codigo OTP enviado por SMS."
                return render(request, self.template_name, {
                    "step": "2",
                    "show_step2": True,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": step1_data,
                    "step2_data": step2_data,
                    "response_json": response_data if response_data else None,
                    "otp_allowed": True,
                    "otp_stage": "verify",
                    "masked_phone": self._mask_phone(phone_number),
                    "otp_autoshow": True,
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })

            try:
                verify_client = TwilioVerifyClient()
                check = verify_client.check_verification(phone_number, otp_code)
            except Exception as e:
                ConsentOTP.objects.filter(id=consent_id).update(
                    status="error",
                    last_error=str(e),
                )
                form_error_message = "No se pudo validar el codigo. Intenta de nuevo."
                return render(request, self.template_name, {
                    "step": "2",
                    "show_step2": True,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": step1_data,
                    "step2_data": step2_data,
                    "response_json": response_data if response_data else None,
                    "otp_allowed": True,
                    "otp_stage": "verify",
                    "masked_phone": self._mask_phone(phone_number),
                    "otp_autoshow": True,
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })

            if check.status != "approved":
                ConsentOTP.objects.filter(id=consent_id).update(
                    status=check.status or "rejected",
                    verification_check_sid=getattr(check, "sid", ""),
                    last_error="OTP no aprobado",
                )
                form_error_message = "Codigo OTP invalido o expirado."
                return render(request, self.template_name, {
                    "step": "2",
                    "show_step2": True,
                    "show_step3": False,
                    "form_error_message": form_error_message,
                    "step1_data": step1_data,
                    "step2_data": step2_data,
                    "response_json": response_data if response_data else None,
                    "otp_allowed": True,
                    "otp_stage": "verify",
                    "masked_phone": self._mask_phone(phone_number),
                    "otp_autoshow": True,
                    "agencias_villavicencio": self.AGENCIAS_VILLAVICENCIO,
                    "agencias_municipios": self.AGENCIAS_MUNICIPIOS,
                    "corresponsales": self.CORRESPONSALES,
                })

            issued_at = timezone.now()
            pdf_data = build_consent_data(
                full_name=full_name,
                id_number=step1_data.get("idNumber", ""),
                id_type=DOCUMENT_TYPE_LABELS.get(step1_data.get("idType", ""), step1_data.get("idType", "")),
                phone_number=phone_number,
                place=place,
                issued_at=issued_at,
            )
            pdf_bytes = fill_consent_pdf(pdf_data)
            filename = f"consent_{step1_data.get('idNumber','')}_{issued_at.strftime('%Y%m%d_%H%M%S')}.pdf"

            ConsentOTP.objects.filter(id=consent_id).update(
                status=check.status,
                verification_check_sid=getattr(check, "sid", ""),
                verified_at=issued_at,
                decision=decision_value,
                risk_level=risk_value,
                preselecta_query_id=preselecta_query_id,
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


class HistorialPagoView(View):
    template_name = "integrations/historial_pago.html"
    ID_TYPE_LABELS = {
    "1": "Cedula de Ciudadania",
    "2": "NIT",
    "3": "NIT de extranjeria",
    "4": "Cedula de extranjeria",
    "5": "Pasaporte",
    "6": "Carne diplomatico",
}

    def get(self, request, *args, **kwargs):
        if not request.session.get("otp_verified"):
            messages.error(request, "Debes validar el OTP antes de consultar el historial.")
            return redirect("integrations:consulta")

        historial_data = request.session.get("historial_data") or {}
        id_type = historial_data.get("person_id_type", "")
        is_juridica = str(id_type) == "2"
        return render(request, self.template_name, {
            "historial_data": historial_data,
            "historial_id_type_label": self.ID_TYPE_LABELS.get(str(id_type), str(id_type)),
            "is_juridica": is_juridica,
        })

    def post(self, request, *args, **kwargs):
        if not request.session.get("otp_verified"):
            messages.error(request, "Debes validar el OTP antes de consultar el historial.")
            return redirect("integrations:consulta")

        historial_data = request.session.get("historial_data") or {}
        person_id_number = historial_data.get("person_id_number", "")
        person_id_type = historial_data.get("person_id_type", "")
        person_last_name = historial_data.get("person_last_name", "")

        cached = CreditReportQuery.find_recent(
            provider=CreditBureauProvider.DATACREDITO,
            person_id_type=str(person_id_type),
            person_id_number=str(person_id_number),
            within_days=30,
        )
        if cached:
            if cached.pdf_file:
                cached.pdf_file.open("rb")
                resp = HttpResponse(cached.pdf_file.read(), content_type="application/pdf")
                resp["Content-Disposition"] = f'inline; filename="{cached.pdf_file.name.split("/")[-1]}"'
                return resp
            messages.warning(request, "Consulta reciente encontrada, pero sin PDF asociado.")
            return redirect("integrations:historial_pago")

        try:
            client = DatacreditoSoapClient()
        except DatacreditoSoapError as exc:
            messages.error(request, f"No se pudo iniciar el servicio: {exc}")
            return redirect("integrations:historial_pago")

        q = CreditReportQuery.objects.create(
            provider=CreditBureauProvider.DATACREDITO,
            operation="consultarHC2",
            person_id_type=str(person_id_type),
            person_id_number=str(person_id_number),
            person_last_name=str(person_last_name),
            product_id=str(client.product_id),
            info_account_type=str(client.info_account_type),
            codes_value="",
            requested_by="",
            requester_ip=request.META.get("REMOTE_ADDR"),
            status="PENDING",
        )

        try:
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

    def get(self, request, *args, **kwargs):
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated or not user.is_staff:
            messages.error(request, "Acceso restringido. Inicia sesión con usuario administrador.")
            return redirect(f"/admin/login/?next={reverse('integrations:admin_auditoria_list')}")

        consents = list(
            ConsentOTP.objects.select_related("preselecta_query").order_by("-created_at")[:200]
        )
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
            rows.append(
                {
                    "consent": consent,
                    "preselecta": preselecta,
                    "report": report,
                    "id_type_label": self.ID_TYPE_LABELS.get(str(consent.id_type), str(consent.id_type or "N/A")),
                }
            )

        return render(request, self.template_name, {
            "rows": rows,
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
        if not user or not user.is_authenticated or not user.is_staff:
            messages.error(request, "Acceso restringido. Inicia sesión con usuario administrador.")
            return redirect(f"/admin/login/?next={reverse('integrations:admin_auditoria_detail', args=[consent_id])}")

        consent = ConsentOTP.objects.select_related("preselecta_query").filter(id=consent_id).first()
        if not consent:
            messages.error(request, "Registro no encontrado.")
            return redirect("/admin-auditoria/")

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

        return render(
            request,
            self.template_name,
            {
                "consent": consent,
                "preselecta": consent.preselecta_query,
                "report": report,
                "preselecta_summary": summary,
                "id_type_label": self.ID_TYPE_LABELS.get(str(consent.id_type), str(consent.id_type or "N/A")),
                "id_type_labels": self.ID_TYPE_LABELS,
            },
        )

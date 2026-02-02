import hashlib
import json

from django.conf import settings
from django.core.files.base import ContentFile
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CreditBureauProvider, CreditReportQuery
from .serializers import HC2SoapJuridicaSerializer, HC2SoapNaturalSerializer
from .services.datacredito_report import DatacreditoReportError, xml_to_pdf_bytes
from .services.datacredito_soap import DatacreditoSoapClient, DatacreditoSoapError


WINDOW_DAYS = 30


def _requester_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _requested_by(request) -> str:
    user = getattr(request, "user", None)
    if user and getattr(user, "is_authenticated", False):
        return getattr(user, "username", "") or ""
    return request.META.get("REMOTE_USER", "") or ""


def _pdf_response_bytes(pdf_bytes: bytes, filename: str) -> HttpResponse:
    resp = HttpResponse(pdf_bytes, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp


def _pdf_response_file(file_field, filename: str) -> FileResponse:
    file_field.open("rb")
    resp = FileResponse(file_field, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{filename}"'
    return resp


def _save_pdf(query: CreditReportQuery, pdf_bytes: bytes) -> None:
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    query.pdf_sha256 = sha
    query.mark_success()
    filename = "historial.pdf"
    query.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
    query.save(update_fields=["pdf_sha256", "pdf_file", "status", "consulted_at", "updated_at"])


def _cached_response(cached: CreditReportQuery, as_xml: bool) -> HttpResponse:
    if as_xml:
        return HttpResponse(cached.soap_response_xml or "", content_type="application/xml")
    if cached.pdf_file:
        filename = cached.pdf_file.name.split("/")[-1]
        return _pdf_response_file(cached.pdf_file, filename)
    return Response({"ok": False, "message": "PDF no disponible en cache"}, status=404)


class HC2SoapNaturalPdfView(APIView):
    authentication_classes = []
    permission_classes = []
    def post(self, request):
        if settings.DEBUG:
            print(f"HC2SoapNaturalPdfView hit: {request.path}")
        s = HC2SoapNaturalSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        as_xml = request.query_params.get("as_xml") in ("1", "true", "True", "yes")

        cached = CreditReportQuery.find_recent(
            provider=CreditBureauProvider.DATACREDITO,
            person_id_type=s.validated_data["person_id_type"],
            person_id_number=s.validated_data["person_id_number"],
            within_days=WINDOW_DAYS,
        )
        if cached:
            return _cached_response(cached, as_xml)

        try:
            client = DatacreditoSoapClient()
        except DatacreditoSoapError as exc:
            return Response({"ok": False, "error": str(exc)}, status=400)

        q = CreditReportQuery.objects.create(
            provider=CreditBureauProvider.DATACREDITO,
            operation="consultarHC2",
            person_id_type=str(s.validated_data["person_id_type"]),
            person_id_number=str(s.validated_data["person_id_number"]),
            person_last_name=str(s.validated_data["person_last_name"]),
            product_id=str(client.product_id),
            info_account_type=str(client.info_account_type),
            codes_value=str(s.validated_data.get("codes_value", "")),
            requested_by=_requested_by(request),
            requester_ip=_requester_ip(request),
            status="PENDING",
        )

        try:
            codes_value = (s.validated_data.get("codes_value") or "").strip()
            parameters = []
            if codes_value:
                parameters.append({"tipo": "0", "nombre": "codigos", "valor": codes_value})

            result = client.consultar_hc2(
                identificacion=s.validated_data["person_id_number"],
                tipo_identificacion=s.validated_data["person_id_type"],
                primer_apellido=s.validated_data["person_last_name"],
                parameters=parameters or None,
                celebrity_id=s.validated_data.get("celebrity_id", ""),
            )
        except DatacreditoSoapError as exc:
            q.mark_failed(error_message=str(exc))
            q.save(update_fields=["status", "http_status", "error_code", "error_message", "consulted_at", "updated_at"])
            return Response({"ok": False, "error": str(exc)}, status=502)

        xml = result.get("xml")
        if not xml:
            q.mark_failed(error_message="No se pudo extraer XML del response SOAP")
            q.save(update_fields=["status", "error_message", "consulted_at", "updated_at"])
            payload = {"ok": False, "message": "No se pudo extraer XML del response SOAP"}
            if settings.DEBUG:
                payload["raw"] = str(result.get("raw", ""))[:5000]
            return Response(payload, status=502)

        q.soap_request_xml = result.get("soap_request_xml", "")
        q.soap_response_xml = xml
        q.save(update_fields=["soap_request_xml", "soap_response_xml", "updated_at"])

        try:
            pdf_bytes = xml_to_pdf_bytes(xml)
        except DatacreditoReportError as exc:
            q.mark_failed(error_message=str(exc))
            q.save(update_fields=["status", "error_message", "consulted_at", "updated_at"])
            return Response({"ok": False, "error": str(exc)}, status=500)

        _save_pdf(q, pdf_bytes)

        if as_xml:
            return HttpResponse(xml, content_type="application/xml")

        filename = f"hc2_{s.validated_data['person_id_number']}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return _pdf_response_bytes(pdf_bytes, filename)


class HC2SoapJuridicaPdfView(APIView):
    authentication_classes = []
    permission_classes = []
    def post(self, request):
        s = HC2SoapJuridicaSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        as_xml = request.query_params.get("as_xml") in ("1", "true", "True", "yes")

        cached = CreditReportQuery.find_recent(
            provider=CreditBureauProvider.DATACREDITO,
            person_id_type=s.validated_data["person_id_type"],
            person_id_number=s.validated_data["person_id_number"],
            within_days=WINDOW_DAYS,
        )
        if cached:
            return _cached_response(cached, as_xml)

        try:
            client = DatacreditoSoapClient()
        except DatacreditoSoapError as exc:
            return Response({"ok": False, "error": str(exc)}, status=400)

        q = CreditReportQuery.objects.create(
            provider=CreditBureauProvider.DATACREDITO,
            operation="consultarHC2PJ",
            person_id_type=str(s.validated_data["person_id_type"]),
            person_id_number=str(s.validated_data["person_id_number"]),
            person_last_name=str(s.validated_data["razon_social"]),
            product_id=str(client.product_id),
            info_account_type=str(client.info_account_type),
            codes_value=str(s.validated_data.get("codes_value", "")),
            requested_by=_requested_by(request),
            requester_ip=_requester_ip(request),
            status="PENDING",
        )

        try:
            codes_value = (s.validated_data.get("codes_value") or "").strip()
            parameters = []
            if codes_value:
                parameters.append({"tipo": "0", "nombre": "codigos", "valor": codes_value})

            result = client.consultar_hc2pj(
                identificacion=s.validated_data["person_id_number"],
                tipo_identificacion=s.validated_data["person_id_type"],
                primer_apellido=s.validated_data["razon_social"],
                parameters=parameters or None,
                celebrity_id=s.validated_data.get("celebrity_id", ""),
            )
        except DatacreditoSoapError as exc:
            q.mark_failed(error_message=str(exc))
            q.save(update_fields=["status", "http_status", "error_code", "error_message", "consulted_at", "updated_at"])
            return Response({"ok": False, "error": str(exc)}, status=502)

        xml = result.get("xml")
        if not xml:
            q.mark_failed(error_message="No se pudo extraer XML del response SOAP")
            q.save(update_fields=["status", "error_message", "consulted_at", "updated_at"])
            payload = {"ok": False, "message": "No se pudo extraer XML del response SOAP"}
            if settings.DEBUG:
                payload["raw"] = str(result.get("raw", ""))[:5000]
            return Response(payload, status=502)

        q.soap_request_xml = result.get("soap_request_xml", "")
        q.soap_response_xml = xml
        q.save(update_fields=["soap_request_xml", "soap_response_xml", "updated_at"])

        try:
            pdf_bytes = xml_to_pdf_bytes(xml)
        except DatacreditoReportError as exc:
            q.mark_failed(error_message=str(exc))
            q.save(update_fields=["status", "error_message", "consulted_at", "updated_at"])
            return Response({"ok": False, "error": str(exc)}, status=500)

        _save_pdf(q, pdf_bytes)

        if as_xml:
            return HttpResponse(xml, content_type="application/xml")

        filename = f"hc2pj_{s.validated_data['person_id_number']}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return _pdf_response_bytes(pdf_bytes, filename)

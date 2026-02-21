"""
Cliente SOAP para DataCredito con WS-Security (UsernameToken + Signature + Timestamp).
"""

import os
import hashlib
import html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from lxml import etree
from zeep import Client, Settings
from zeep.transports import Transport
from zeep.wsse.signature import BinarySignature
from zeep.wsse.username import UsernameToken
from zeep.plugins import HistoryPlugin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from django.conf import settings


class DatacreditoSoapError(Exception):
    pass


def _getenv_required(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise DatacreditoSoapError(f"Variable de entorno requerida no configurada: {key}")
    return val


def _cert_path(rel: str) -> str:
    base = Path(settings.BASE_DIR)
    full = (base / rel).resolve()
    if not full.exists():
        raise DatacreditoSoapError(f"Certificado no encontrado: {full}")
    return str(full)


class DatacreditoSoapClient:
    def __init__(self):
        self.wsdl_url = _getenv_required("DATACREDITO_WSDL_URL")
        self.soap_user = _getenv_required("DATACREDITO_SOAP_USER")
        self.soap_password = _getenv_required("DATACREDITO_SOAP_PASSWORD")

        self.okta_user = os.getenv("DATACREDITO_OKTA_USER") or _getenv_required("DATACREDITO_OKTA_USER")
        self.okta_password = os.getenv("DATACREDITO_OKTA_PASSWORD") or _getenv_required("DATACREDITO_OKTA_PASSWORD")

        self.product_id = os.getenv("DATACREDITO_PRODUCT_ID", "64")
        self.info_account_type = os.getenv("DATACREDITO_INFO_ACCOUNT_TYPE", "1")
        self.server_ip = os.getenv("DATACREDITO_SERVER_IP", "")

        cert_rel = _getenv_required("DATACREDITO_SOAP_CERT")
        key_rel = _getenv_required("DATACREDITO_SOAP_KEY")
        fullchain_rel = os.getenv("DATACREDITO_SOAP_FULLCHAIN", cert_rel)

        self.cert_path = _cert_path(cert_rel)
        self.key_path = _cert_path(key_rel)
        self.fullchain_path = _cert_path(fullchain_rel)

        self.log_xml = os.getenv("DATACREDITO_SOAP_LOG_XML", "1") == "1"
        self._client = self._create_zeep_client()

    def _create_session(self) -> requests.Session:
        session = requests.Session()
        session.cert = (self.cert_path, self.key_path)
        ca_bundle = os.getenv("DATACREDITO_SOAP_CA_BUNDLE", "").strip()
        tls_verify = os.getenv("DATACREDITO_SOAP_TLS_VERIFY", "1") not in ("0", "false", "False")
        session.verify = ca_bundle if ca_bundle else tls_verify

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[502, 503, 504],
            allowed_methods=["POST"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.headers.update(
            {
                "User-Agent": "DataCreditoSoapClient/1.0",
                "Content-Type": "text/xml; charset=utf-8",
            }
        )
        return session

    def _create_zeep_client(self) -> Client:
        session = self._create_session()
        transport = Transport(session=session, timeout=30)
        history = HistoryPlugin()
        zeep_settings = Settings(strict=False, xml_huge_tree=True, xsd_ignore_sequence_order=True)

        client = Client(self.wsdl_url, transport=transport, settings=zeep_settings, plugins=[history])
        client.wsse = UsernameToken(username=self.okta_user, password=self.okta_password, use_digest=False)
        self._history = history
        return client

    def _build_soap_envelope_manual(
        self,
        operation: str,
        identificacion: str,
        tipo_identificacion: str,
        primer_apellido: str,
        parameters: list | None = None,
        celebrity_id: str = "1",
    ) -> str:
        def _safe(value) -> str:
            return xml_escape(str(value or ""))

        minimal_fields = os.getenv("DATACREDITO_SOAP_MINIMAL_FIELDS", "0") in ("1", "true", "True")
        params_xml = ""
        if parameters and not minimal_fields:
            for p in parameters:
                params_xml += f"""
                <ns1:parametro>
                    <ns1:tipo>{_safe(p.get('tipo', '0'))}</ns1:tipo>
                    <ns1:nombre>{_safe(p.get('nombre', ''))}</ns1:nombre>
                    <ns1:valor>{_safe(p.get('valor', ''))}</ns1:valor>
                </ns1:parametro>"""

        timestamp_id = f"TS-{hashlib.sha1(os.urandom(16)).hexdigest()[:16].upper()}"
        body_id = f"id-{hashlib.sha1(os.urandom(16)).hexdigest()[:16].upper()}"

        ttl_seconds = int(os.getenv("DATACREDITO_SOAP_TTL_SECONDS", "300"))
        skew_seconds = int(os.getenv("DATACREDITO_SOAP_TIME_SKEW_SECONDS", "120"))
        now = datetime.now(timezone.utc)
        created_dt = now - timedelta(seconds=skew_seconds)
        expires_dt = now + timedelta(seconds=ttl_seconds)
        created = created_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        expires = expires_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        envelope = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:ns1="http://ws.hc2.dc.com/v1"
    xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd"
    xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
    <soapenv:Header>
        <wsse:Security soapenv:mustUnderstand="1">
            <wsu:Timestamp wsu:Id="{timestamp_id}">
                <wsu:Created>{created}</wsu:Created>
                <wsu:Expires>{expires}</wsu:Expires>
            </wsu:Timestamp>
            <wsse:UsernameToken>
                <wsse:Username>{self.okta_user}</wsse:Username>
                <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordText">{self.okta_password}</wsse:Password>
            </wsse:UsernameToken>
        </wsse:Security>
    </soapenv:Header>
    <soapenv:Body wsu:Id="{body_id}">
        <ns1:{operation}>
            <ns1:solicitud>
                <ns1:clave>{_safe(self.soap_password)}</ns1:clave>
                <ns1:identificacion>{_safe(identificacion)}</ns1:identificacion>
                <ns1:primerApellido>{_safe(primer_apellido)}</ns1:primerApellido>
                <ns1:producto>{_safe(self.product_id)}</ns1:producto>
                <ns1:tipoIdentificacion>{_safe(tipo_identificacion)}</ns1:tipoIdentificacion>
                <ns1:usuario>{_safe(self.soap_user)}</ns1:usuario>"""

        if not minimal_fields:
            envelope += f"""
                <ns1:InfoTipoCuenta>{_safe(self.info_account_type)}</ns1:InfoTipoCuenta>
                <ns1:celebrityId>{_safe(celebrity_id)}</ns1:celebrityId>"""

        if params_xml:
            envelope += f"""
                <ns1:parametros>{params_xml}
                </ns1:parametros>"""

        envelope += f"""
            </ns1:solicitud>
        </ns1:{operation}>
    </soapenv:Body>
</soapenv:Envelope>"""

        envelope_el = etree.fromstring(envelope.encode("utf-8"))
        try:
            signer = BinarySignature(self.key_path, self.cert_path)
            envelope_el, _ = signer.apply(envelope_el, {})
            self._reorder_security_header(envelope_el)
        except Exception as exc:
            raise DatacreditoSoapError(f"Error firmando SOAP: {str(exc)}") from exc

        return etree.tostring(envelope_el, encoding="utf-8", xml_declaration=True).decode("utf-8")

    def _reorder_security_header(self, envelope_el) -> None:
        ns = {
            "soapenv": "http://schemas.xmlsoap.org/soap/envelope/",
            "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
            "wsu": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
            "ds": "http://www.w3.org/2000/09/xmldsig#",
        }
        header = envelope_el.find("soapenv:Header", namespaces=ns)
        if header is None:
            return
        security = header.find("wsse:Security", namespaces=ns)
        if security is None:
            return

        timestamp = security.find("wsu:Timestamp", namespaces=ns)
        username = security.find("wsse:UsernameToken", namespaces=ns)
        bintok = security.find("wsse:BinarySecurityToken", namespaces=ns)
        signature = security.find("ds:Signature", namespaces=ns)

        security[:] = []
        for node in (timestamp, username, bintok, signature):
            if node is not None:
                security.append(node)

    def _send_soap_request(self, envelope_xml: str) -> dict:
        service_url = os.getenv("DATACREDITO_SOAP_ADDRESS", "").strip()
        if not service_url:
            services = list(self._client.wsdl.services.values())
            if not services:
                raise DatacreditoSoapError("No se encontraron servicios en el WSDL")
            ports = list(services[0].ports.values())
            if not ports:
                raise DatacreditoSoapError("No se encontraron puertos en el WSDL")
            service_url = ports[0].binding_options.get("address", "")
        if not service_url:
            raise DatacreditoSoapError("No se pudo resolver la URL del servicio SOAP")

        if self.log_xml:
            print("--- SOAP REQUEST sent ---")

        session = self._create_session()
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'}

        try:
            response = session.post(
                service_url,
                data=envelope_xml.encode("utf-8"),
                headers=headers,
                timeout=30,
            )
            if self.log_xml:
                print(f"--- SOAP RESPONSE status={response.status_code} ---")
            response.raise_for_status()
            return {"raw": response.text, "status_code": response.status_code, "soap_request_xml": envelope_xml}
        except requests.exceptions.RequestException as exc:
            raise DatacreditoSoapError(f"Error en request SOAP: {str(exc)}") from exc

    def _extract_xml_from_response(self, soap_response: str) -> str:
        try:
            root = etree.fromstring(soap_response.encode("utf-8"))
            result = root.xpath(
                "//*[local-name()='consultarHC2Return' or local-name()='consultarHC2PJReturn']/text()"
            )
            if not result:
                result = root.xpath("//*[local-name()='return']/text()")
            if result:
                raw_xml = result[0]
                return html.unescape(raw_xml).strip()
            raise DatacreditoSoapError("No se encontro el XML de respuesta en el SOAP envelope")
        except etree.XMLSyntaxError as exc:
            raise DatacreditoSoapError(f"Error parseando respuesta SOAP: {str(exc)}") from exc

    def consultar_hc2(
        self,
        identificacion: str,
        tipo_identificacion: str,
        primer_apellido: str,
        parameters: list | None = None,
        celebrity_id: str = "1",
    ) -> dict:
        envelope = self._build_soap_envelope_manual(
            operation="consultarHC2",
            identificacion=identificacion,
            tipo_identificacion=tipo_identificacion,
            primer_apellido=primer_apellido,
            parameters=parameters,
            celebrity_id=celebrity_id,
        )
        result = self._send_soap_request(envelope)
        try:
            xml_content = self._extract_xml_from_response(result["raw"])
            result["xml"] = xml_content
        except DatacreditoSoapError as exc:
            result["xml"] = None
            result["error"] = str(exc)
        return result

    def consultar_hc2pj(
        self,
        identificacion: str,
        tipo_identificacion: str,
        primer_apellido: str,
        parameters: list | None = None,
        celebrity_id: str = "1",
    ) -> dict:
        envelope = self._build_soap_envelope_manual(
            operation="consultarHC2PJ",
            identificacion=identificacion,
            tipo_identificacion=tipo_identificacion,
            primer_apellido=primer_apellido,
            parameters=parameters,
            celebrity_id=celebrity_id,
        )
        result = self._send_soap_request(envelope)
        try:
            xml_content = self._extract_xml_from_response(result["raw"])
            result["xml"] = xml_content
        except DatacreditoSoapError as exc:
            result["xml"] = None
            result["error"] = str(exc)
        return result

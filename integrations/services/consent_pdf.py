import os
import io
from dataclasses import dataclass

from django.conf import settings
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, BooleanObject, TextStringObject
from reportlab.pdfgen import canvas


@dataclass(frozen=True)
class ConsentPdfData:
    full_name: str
    id_number: str
    id_type: str
    phone_number: str
    place: str
    issued_at: str
    day: str
    month_name: str
    year: str
    authorized_channel: str = ""
    authorized_otp_masked: str = ""
    authorized_otp_full: str = ""
    authorized_destination_full: str = ""


def _template_path() -> str:
    preferred = os.path.join(
        settings.BASE_DIR,
        "static",
        "pdf",
        "COLO-FO.023 Autorización de consulta y reporte a centrales de riesgos FINAL.pdf",
    )
    if os.path.exists(preferred):
        return preferred
    fallback = os.path.join(settings.BASE_DIR, "static", "pdf", "autorizacion_centrales_riesgo.pdf")
    return fallback


def _month_name_es(month_number: int) -> str:
    months = [
        "",
        "Enero",
        "Febrero",
        "Marzo",
        "Abril",
        "Mayo",
        "Junio",
        "Julio",
        "Agosto",
        "Septiembre",
        "Octubre",
        "Noviembre",
        "Diciembre",
    ]
    return months[month_number] if 1 <= month_number <= 12 else ""


def build_consent_data(
    full_name: str,
    id_number: str,
    id_type: str,
    phone_number: str,
    place: str,
    issued_at,
    authorized_channel: str = "",
    authorized_otp_masked: str = "",
    authorized_otp_full: str = "",
    authorized_destination_full: str = "",
):
    day = str(issued_at.day)
    month_name = _month_name_es(issued_at.month)
    year = str(issued_at.year)
    issued_str = issued_at.strftime("%Y-%m-%d %H:%M")
    return ConsentPdfData(
        full_name=full_name,
        id_number=id_number,
        id_type=id_type,
        phone_number=phone_number,
        place=(place or "").strip().upper() or "VILLAVICENCIO",
        issued_at=issued_str,
        day=day,
        month_name=month_name,
        year=year,
        authorized_channel=(authorized_channel or "").strip().lower(),
        authorized_otp_masked=(authorized_otp_masked or "").strip(),
        authorized_otp_full=(authorized_otp_full or "").strip(),
        authorized_destination_full=(authorized_destination_full or "").strip(),
    )


def fill_consent_pdf(data: ConsentPdfData) -> bytes:
    reader = PdfReader(_template_path())
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    if "/AcroForm" in reader.trailer["/Root"]:
        writer._root_object.update(
            {NameObject("/AcroForm"): reader.trailer["/Root"]["/AcroForm"]}
        )
        writer._root_object["/AcroForm"].update(
            {NameObject("/NeedAppearances"): BooleanObject(True)}
        )
        writer._root_object["/AcroForm"].update(
            {NameObject("/DA"): TextStringObject("/Helvetica 10 Tf 0 g")}
        )

    def _checkbox_on_value(field) -> str:
        try:
            ap = field.get("/AP", {}).get("/N", {})
            for key in ap.keys():
                name = str(key)
                if "Off" not in name:
                    return name.replace("/", "")
        except Exception:
            pass
        return "Yes"

    fields_meta = reader.get_fields() or {}
    checkbox_on = _checkbox_on_value(fields_meta.get("Check Box10", {}))

    fields = {
        "Check Box10": checkbox_on,
        "Check Box11": "Off",
        "firma en": data.place,
        "a los": data.day,
        "días del mes de": data.month_name,
        "del año": data.year,
        "Nombre": data.full_name,
        "CC  NIT": data.id_number,
        "Telefono": data.phone_number,
    }

    def _overlay_text(rect, text: str, page_width: float, page_height: float) -> bytes:
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(page_width, page_height))
        x0, y0, x1, y1 = rect
        width = max(x1 - x0, 1)
        height = max(y1 - y0, 1)
        font_size = max(6, min(10, height * 0.8))
        c.setFont("Helvetica-Bold", font_size)
        c.drawCentredString(x0 + width / 2.0, y0 + (height - font_size) / 2.0, text)
        c.showPage()
        c.save()
        return buf.getvalue()

    def _overlay_footer_text(text: str, page_width: float, page_height: float) -> bytes:
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(page_width, page_height))
        c.setFont("Helvetica", 8)
        c.setFillGray(0.35)
        c.drawCentredString(page_width / 2.0, 14, text)
        c.showPage()
        c.save()
        return buf.getvalue()

    for page in writer.pages:
        writer.update_page_form_field_values(page, fields)
        # Force checkbox appearance values for PDF viewers
        for annot in page.get("/Annots", []) or []:
            field = annot.get_object()
            if field.get("/T") == "Check Box10":
                field.update({
                    NameObject("/V"): NameObject(f"/{checkbox_on}"),
                    NameObject("/AS"): NameObject(f"/{checkbox_on}"),
                })
                rect = field.get("/Rect")
                if rect and len(rect) == 4:
                    overlay_pdf = _overlay_text(
                        rect,
                        "SI",
                        float(page.mediabox.width),
                        float(page.mediabox.height),
                    )
                    overlay_page = PdfReader(io.BytesIO(overlay_pdf)).pages[0]
                    page.merge_page(overlay_page)
            elif field.get("/T") == "Check Box11":
                field.update({
                    NameObject("/V"): NameObject("/Off"),
                    NameObject("/AS"): NameObject("/Off"),
                })

    if writer.pages:
        channel = (data.authorized_channel or "").lower()
        if channel == "sms":
            destination = data.authorized_destination_full or data.phone_number or "N/A"
            footer_text = f"Documento autorizado mediante OTP vía SMS al número {destination}"
        elif channel == "email":
            otp_value = data.authorized_otp_full or data.authorized_otp_masked or "N/A"
            destination = data.authorized_destination_full or "N/A"
            footer_text = f"Documento autorizado mediante OTP {otp_value} vía EMAIL al correo {destination}"
        else:
            otp_value = data.authorized_otp_full or data.authorized_otp_masked or "N/A"
            footer_text = f"Documento autorizado mediante OTP {otp_value}"
        first_page = writer.pages[0]
        footer_pdf = _overlay_footer_text(
            footer_text,
            float(first_page.mediabox.width),
            float(first_page.mediabox.height),
        )
        footer_page = PdfReader(io.BytesIO(footer_pdf)).pages[0]
        first_page.merge_page(footer_page)

    output_io = io.BytesIO()
    writer.write(output_io)
    return output_io.getvalue()

import os
import io
from dataclasses import dataclass

from django.conf import settings
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, BooleanObject, TextStringObject


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


def build_consent_data(full_name: str, id_number: str, id_type: str, phone_number: str, place: str, issued_at):
    day = str(issued_at.day)
    month_name = _month_name_es(issued_at.month)
    year = str(issued_at.year)
    issued_str = issued_at.strftime("%Y-%m-%d %H:%M")
    return ConsentPdfData(
        full_name=full_name,
        id_number=id_number,
        id_type=id_type,
        phone_number=phone_number,
        place=place,
        issued_at=issued_str,
        day=day,
        month_name=month_name,
        year=year,
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

    for page in writer.pages:
        writer.update_page_form_field_values(page, fields)

    output_io = io.BytesIO()
    writer.write(output_io)
    return output_io.getvalue()

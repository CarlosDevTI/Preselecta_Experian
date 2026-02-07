import base64
import io
import xml.etree.ElementTree as ET
from datetime import datetime
from html import unescape
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from lxml import etree as LET


class DatacreditoReportError(Exception):
    pass


def _coerce_xml_string(xml_input) -> str:
    if xml_input is None:
        return ""
    if isinstance(xml_input, bytes):
        text = xml_input.decode("utf-8", errors="ignore")
    else:
        text = str(xml_input)
    text = text.replace("\ufeff", "").strip()
    if not text:
        return ""
    if "&lt;" in text and "&gt;" in text:
        text = unescape(text)
    # If extra text appears before XML, keep only XML payload.
    first_lt = text.find("<")
    if first_lt > 0:
        text = text[first_lt:]
    # Favor the payload node if wrapper/noise is present.
    idx = text.find("<Informes")
    if idx > 0:
        text = text[idx:]
    return text.strip()


def _parse_root(xml_input):
    xml_str = _coerce_xml_string(xml_input)
    if not xml_str:
        raise DatacreditoReportError("XML vacio en respuesta del proveedor")
    try:
        return ET.fromstring(xml_str)
    except Exception as strict_exc:
        # Fallback parser for malformed XML (e.g. bad entities or noise in payload).
        try:
            parser = LET.XMLParser(recover=True, huge_tree=True)
            root = LET.fromstring(xml_str.encode("utf-8", errors="ignore"), parser=parser)
            if root is None:
                raise ValueError("LXML recover parser returned no root")
            recovered_xml = LET.tostring(root, encoding="utf-8")
            return ET.fromstring(recovered_xml)
        except Exception as recover_exc:
            preview = (xml_str[:240] + "...") if len(xml_str) > 240 else xml_str
            raise DatacreditoReportError(
                f"XML invalido o no parseable. Inicio recibido: {preview}"
            ) from recover_exc


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _clean_text(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value)
    if "Ã" in text or "Â" in text:
        try:
            text = text.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
        except Exception:
            pass
    return text


def _format_number(value: str | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text in ("", "-", "--", "N", "NN", "N/A"):
        return text
    try:
        num = float(text)
    except Exception:
        return text
    if abs(num - int(num)) < 1e-9:
        return f"{int(num):,}".replace(",", ".")
    # Use comma as decimal separator for readability
    return f"{num:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


CALIFICACION_MAP = {
    "1": "A",
    "2": "B",
    "3": "C",
    "4": "D",
    "5": "E",
    "6": "AA",
    "7": "BB",
    "8": "CC",
    "9": "K",
    "-": "-",
    "": "-",
    None: "-",
}



SITUACION_TITULAR_MAP = {
    "0": "Normal",
    "1": "Concordato",
    "2": "Liquidacion Forzosa",
    "3": "Liquidacion Voluntaria",
    "4": "Proceso de Reorganizacion",
    "5": "Ley 550",
    "6": "Ley 1116",
    "7": "Otra",
    "8": "Liquidacion Patrimonial",
    "": "-",
    None: "-",
}



GARANTE_MAP = {
    "00": "Deudor Principal",
    "01": "Codeudor",
    "02": "Codeudor",
    "03": "Codeudor",
    "04": "Avalista",
    "05": "Deudor solidario",
    "06": "Coarrendatario",
    "07": "Otros Garantes",
    "08": "Fiador",
    "96": "Cotitular",
    "97": "Comunal",
}


def _map_garante(value: str) -> str:
    raw = str(value or "").strip()
    if raw in ("", "-", "--", "N", "NN", "N/A"):
        return "-"
    if raw.isdigit():
        num = int(raw)
        if 9 <= num <= 95 or 98 <= num <= 99:
            return "No Aplica"
    return GARANTE_MAP.get(raw, raw)
def _map_situacion_titular(value: str) -> str:
    raw = str(value or "").strip()
    return SITUACION_TITULAR_MAP.get(raw, raw or "-")


TIPO_CUENTA_LABELS = {
    "CAB": "CARTERA BANCARIA",
    "TDC": "TARJETAS DE CREDITO",
    "CON": "CREDITOS DE CONSUMO",
    "MCR": "CARTERA MICROCREDITO",
    "CTC": "CARTERA TELEFONIA CELULAR",
    "CAC": "CART. COOP DE AHORRO Y CREDITO",
    "AHO": "CUENTAS DE AHORRO BANCARIA",
    "CDC": "CARTERA DE COMUNICACIONES",
    "COM": "CARTERA DE EQUIPOS",
    "CBR": "CARTERA BANCARIA ROTATIVA",
    "CBF": "FIDUCIARIA",
    "CBD": "CARTERA BANCARIA DIGITAL",
    "CFE": "CARTERA FONDOS DE EMPLEADOS",
    "CCB": "CUENTA CORRIENTE BANCARIA",
    "CCD": "CUENTA CORRIENTE DIGITAL",
    "CDT": "CDT",
    "APD": "ALMACEN POR DEPARTAMENTOS",
    "ADP": "ALMACEN POR DEPARTAMENTOS",
    "CVE": "CARTERA VESTUARIO",
}


def _map_tipo_cuenta(value: str) -> str:
    raw = str(value or "").strip()
    return TIPO_CUENTA_LABELS.get(raw, raw or "-")
def _map_calificacion(value: str) -> str:
    raw = str(value or "").strip()
    return CALIFICACION_MAP.get(raw, raw or "-")

def _parse_number(value: str | None) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if text in ("", "-", "--", "N", "NN", "N/A"):
        return 0.0
    try:
        return float(text)
    except Exception:
        return 0.0


def _format_month_label(date_str: str) -> str:
    try:
        parts = date_str.split("-")
        year = parts[0][-2:]
        month = int(parts[1])
    except Exception:
        return date_str
    month_map = {
        1: "Ene", 2: "Feb", 3: "Mar", 4: "Abr", 5: "May", 6: "Jun",
        7: "Jul", 8: "Ago", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dic",
    }
    return f"{month_map.get(month, month)} {year}"


def _format_date_compact(date_str: str | None) -> str:
    if not date_str:
        return ""
    text = str(date_str).strip()
    if text in ("-", "--", "N", "NN", "N/A"):
        return text
    # Expecting YYYY-MM-DD
    parts = text.split("-")
    if len(parts) == 3 and all(parts):
        return f"{parts[0]}{parts[1]}{parts[2]}"
    return text



def _wrap_behavior(value: str | None, width: int = 18) -> str:
    if value is None:
        return "-"
    raw = str(value).strip()
    if raw in ("", "-", "--", "N", "NN", "N/A"):
        return raw
    parts = [raw[i:i+width] for i in range(0, len(raw), width)]
    return "<br>".join(parts)

def _attr(elem: ET.Element | None, key: str, default: str = "") -> str:
    if elem is None:
        return default
    return _clean_text(elem.attrib.get(key, default))


def _parse_xml(xml_str: str) -> dict:
    root = _parse_root(xml_str)

    informe = root.find("Informe")
    if informe is None:
        raise DatacreditoReportError("XML no contiene nodo Informe")

    natural = informe.find("NaturalNacional")
    identificacion = natural.find("Identificacion") if natural is not None else None
    edad = natural.find("Edad") if natural is not None else None

    scores = []
    score_labels = {
        "DF": "Advance 1.1",
        "BF": "Advance Inclusion",
    }
    for score in informe.findall("Score"):
        razones = [_attr(r, "codigo") for r in score.findall("Razon")]
        scores.append(
            {
                "tipo": score_labels.get(_attr(score, "tipo"), _attr(score, "tipo")),
                "puntaje": _format_number(_attr(score, "puntaje")),
                "fecha": _attr(score, "fecha"),
                "poblacion": _attr(score, "poblacion"),
                "razones": ", ".join([r for r in razones if r]),
            }
        )

    perfil_general_rows = []
    resumen = informe.find("InfoAgregadaMicrocredito/Resumen")
    perfil_general = resumen.find("PerfilGeneral") if resumen is not None else None
    if perfil_general is not None:
        label_map = {
            "CreditosVigentes": "Créditos Vigentes",
            "CreditosCerrados": "Créditos Cerrados",
            "CreditosReestructurados": "Créditos Reestructurados",
            "CreditosRefinanciados": "Créditos Refinanciados",
            "ConsultaUlt6Meses": "Consultas en los ult. 6 Meses",
            "Desacuerdos": "Desacuerdos Vigentes a la Fecha",
            "AntiguedadDesde": "Antigüedad desde",
        }
        for child in list(perfil_general):
            perfil_general_rows.append(
                {
                    "label": label_map.get(child.tag, child.tag),
                    "sectorFinanciero": _attr(child, "sectorFinanciero"),
                    "sectorCooperativo": _attr(child, "sectorCooperativo"),
                    "sectorReal": _attr(child, "sectorReal"),
                    "sectorTelcos": _attr(child, "sectorTelcos"),
                    "totalComoPrincipal": _attr(child, "totalComoPrincipal"),
                    "totalComoCodeudorYOtros": _attr(child, "totalComoCodeudorYOtros"),
                }
            )

    saldos_moras = []
    if resumen is not None:
        vector = resumen.find("VectorSaldosYMoras")
        if vector is not None:
            for item in vector.findall("SaldosYMoras"):
                saldos_moras.append(
                    {
                        "fecha": _attr(item, "fecha"),
                        "saldoDeudaTotal": _format_number(_attr(item, "saldoDeudaTotal")),
                        "saldoDeudaTotalMora": _format_number(_attr(item, "saldoDeudaTotalMora")),
                        "totalCuentasMora": _format_number(_attr(item, "totalCuentasMora")),
                        "morasMaxSectorFinanciero": _attr(item, "morasMaxSectorFinanciero"),
                        "morasMaxSectorReal": _attr(item, "morasMaxSectorReal"),
                        "morasMaxSectorTelcos": _attr(item, "morasMaxSectorTelcos"),
                        "morasMaximas": _attr(item, "morasMaximas"),
                        "numCreditos30": _format_number(_attr(item, "numCreditos30")),
                        "numCreditosMayorIgual60": _format_number(_attr(item, "numCreditosMayorIgual60")),
                    }
                )

    saldos_moras_matrix = {"dates": [], "rows": []}
    if saldos_moras:
        dates = [item["fecha"] for item in saldos_moras]
        labels = [_format_month_label(d) for d in dates]
        saldos_moras_matrix["dates"] = labels
        saldos_moras_matrix["rows"] = [
            {"label": "Saldo Deuda Total (en miles)", "key": "saldoDeudaTotal"},
            {"label": "Saldo Deuda Total en Mora (en miles)", "key": "saldoDeudaTotalMora"},
            {"label": "Total Cuentas Mora", "key": "totalCuentasMora"},
            {"label": "Moras máx Sector Financiero", "key": "morasMaxSectorFinanciero"},
            {"label": "Moras máx Sector Real", "key": "morasMaxSectorReal"},
            {"label": "Moras máx Sector Telcos", "key": "morasMaxSectorTelcos"},
            {"label": "Total Moras Máximas", "key": "morasMaximas"},
            {"label": "Núm créditos con mora > 30", "key": "numCreditos30"},
            {"label": "Núm créditos con mora >= 60", "key": "numCreditosMayorIgual60"},
        ]
        for row in saldos_moras_matrix["rows"]:
            row["values"] = [item.get(row["key"], "") for item in saldos_moras]

    endeudamiento_rows = []
    endeudamiento_grouped = []
    endeudamiento_totals = {"valorInicial": 0.0, "saldoActual": 0.0, "saldoMora": 0.0, "cuotaMes": 0.0}
    if resumen is not None:
        endeudamiento = resumen.find("EndeudamientoActual")
        if endeudamiento is not None:
            sector_names = {"1": "Financiero", "2": "Cooperativo", "3": "Real", "4": "Telcos"}
            for sector in endeudamiento.findall("Sector"):
                sector_name = sector_names.get(_attr(sector, "codSector"), _attr(sector, "codSector"))
                sector_rows = []
                sector_totals = {"valorInicial": 0.0, "saldoActual": 0.0, "saldoMora": 0.0, "cuotaMes": 0.0}
                for tipo_cuenta in sector.findall("TipoCuenta"):
                    tipo = _attr(tipo_cuenta, "tipoCuenta")
                    tipo_label = _map_tipo_cuenta(tipo)
                    for usuario in tipo_cuenta.findall("Usuario"):
                        tipo_usuario = _attr(usuario, "tipoUsuario")
                        for cuenta in usuario.findall("Cuenta"):
                            valor_inicial_raw = _attr(cuenta, "valorInicial")
                            saldo_actual_raw = _attr(cuenta, "saldoActual")
                            saldo_mora_raw = _attr(cuenta, "saldoMora")
                            cuota_mes_raw = _attr(cuenta, "cuotaMes")
                            sector_totals["valorInicial"] += _parse_number(valor_inicial_raw)
                            sector_totals["saldoActual"] += _parse_number(saldo_actual_raw)
                            sector_totals["saldoMora"] += _parse_number(saldo_mora_raw)
                            sector_totals["cuotaMes"] += _parse_number(cuota_mes_raw)
                            endeudamiento_totals["valorInicial"] += _parse_number(valor_inicial_raw)
                            endeudamiento_totals["saldoActual"] += _parse_number(saldo_actual_raw)
                            endeudamiento_totals["saldoMora"] += _parse_number(saldo_mora_raw)
                            endeudamiento_totals["cuotaMes"] += _parse_number(cuota_mes_raw)

                            row = {
                                "sector": sector_name,
                                "tipoCuenta": tipo,
                                "tipoCuentaLabel": tipo_label,
                                "tipoUsuario": tipo_usuario,
                                "estadoActual": _attr(cuenta, "estadoActual"),
                                "calificacion": _map_calificacion(_attr(cuenta, "calificacion")),
                                "valorInicial": _format_number(valor_inicial_raw),
                                "saldoActual": _format_number(saldo_actual_raw),
                                "saldoMora": _format_number(saldo_mora_raw),
                                "cuotaMes": _format_number(cuota_mes_raw),
                                "totalDeudaCarteras": _format_number(_attr(cuenta, "totalDeudaCarteras")),
                            }
                            endeudamiento_rows.append(row)
                            sector_rows.append(row)

                if sector_rows:
                    endeudamiento_grouped.append(
                        {
                            "sector": sector_name,
                            "rows": sector_rows,
                            "totals": {
                                "valorInicial": _format_number(str(sector_totals["valorInicial"])),
                                "saldoActual": _format_number(str(sector_totals["saldoActual"])),
                                "saldoMora": _format_number(str(sector_totals["saldoMora"])),
                                "cuotaMes": _format_number(str(sector_totals["cuotaMes"])),
                            },
                        }
                    )

    tendencia_matrix = {"dates": [], "series": []}
    if resumen is not None:
        tendencia = resumen.find("ImagenTendenciaEndeudamiento")
        if tendencia is not None:
            series_list = tendencia.findall("Series")
            if series_list:
                first = series_list[0]
                dates = [v.attrib.get("fecha", "") for v in first.findall("Valores/Valor")]
                tendencia_matrix["dates"] = [_format_month_label(d) for d in dates if d]
                for serie in series_list:
                    label = _attr(serie, "serie")
                    values = []
                    for v in serie.findall("Valores/Valor"):
                        values.append(_format_number(v.attrib.get("valor", "")))
                    tendencia_matrix["series"].append({"label": label, "values": values})

    analisis_vectores = {"sectors": []}

    info_agregada = informe.find("InfoAgregada")
    cheques = []
    composicion_portafolio = []
    resumen_endeudamiento = []
    historico_saldos = {"dates": [], "rows": [], "totals": []}
    evolucion_deuda_agregada = {"rows": [], "promedio": {}}
    evolucion_deuda_micro = {"sectors": []}

    if info_agregada is not None:
        cheques_node = info_agregada.find("Cheques")
        if cheques_node is not None:
            for tri in cheques_node.findall("Trimestre"):
                cheques.append(
                    {
                        "fecha": _attr(tri, "fecha"),
                        "cantidadDevueltos": _format_number(_attr(tri, "cantidadDevueltos")),
                        "valorDevueltos": _format_number(_attr(tri, "valorDevueltos")),
                        "cantidadPagados": _format_number(_attr(tri, "cantidadPagados")),
                        "valorPagados": _format_number(_attr(tri, "valorPagados")),
                    }
                )

        comp = info_agregada.find("ComposicionPortafolio")
        if comp is not None:
            for tc in comp.findall("TipoCuenta"):
                composicion_portafolio.append(
                    {
                        "tipoCuenta": _map_tipo_cuenta(_attr(tc, "tipo")),
                        "calidadDeudor": _attr(tc, "calidadDeudor"),
                        "porcentaje": _format_number(_attr(tc, "porcentaje")),
                        "cantidad": _format_number(_attr(tc, "cantidad")),
                    }
                )

        resumen_end = info_agregada.find("ResumenEndeudamiento")
        if resumen_end is not None:
            for tri in resumen_end.findall("Trimestre"):
                sectors = []
                for sec in tri.findall("Sector"):
                    carteras = []
                    for cart in sec.findall("Cartera"):
                        carteras.append(
                            {
                                "tipo": _attr(cart, "tipo"),
                                "numeroCuentas": _format_number(_attr(cart, "numeroCuentas")),
                                "valor": _format_number(_attr(cart, "valor")),
                            }
                        )
                    sectors.append(
                        {
                            "sector": _attr(sec, "sector"),
                            "garantiaAdmisible": _format_number(_attr(sec, "garantiaAdmisible")),
                            "garantiaOtro": _format_number(_attr(sec, "garantiaOtro")),
                            "carteras": carteras,
                        }
                    )
                resumen_endeudamiento.append(
                    {
                        "fecha": _attr(tri, "fecha"),
                        "sectors": sectors,
                    }
                )

        hist = info_agregada.find("HistoricoSaldos")
        if hist is not None:
            totals = []
            dates = []
            for tot in hist.findall("Totales"):
                fecha = _attr(tot, "fecha")
                dates.append(fecha)
                totals.append(
                    {
                        "fecha": fecha,
                        "totalCuentas": _format_number(_attr(tot, "totalCuentas")),
                        "cuentasConsideradas": _format_number(_attr(tot, "cuentasConsideradas")),
                        "saldo": _format_number(_attr(tot, "saldo")),
                    }
                )
            historico_saldos["dates"] = [_format_month_label(d) for d in dates if d]
            historico_saldos["totals"] = totals

            for tc in hist.findall("TipoCuenta"):
                tipo = _map_tipo_cuenta(_attr(tc, "tipo"))
                values_map = {}
                for tri in tc.findall("Trimestre"):
                    values_map[_attr(tri, "fecha")] = _format_number(_attr(tri, "saldo"))
                values = [values_map.get(d, "-") for d in dates]
                historico_saldos["rows"].append({"label": tipo, "values": values})

        evo = info_agregada.find("EvolucionDeuda")
        if evo is not None:
            for tri in evo.findall("Trimestre"):
                evolucion_deuda_agregada["rows"].append(
                    {
                        "fecha": _attr(tri, "fecha"),
                        "cupoTotal": _format_number(_attr(tri, "cupoTotal")),
                        "saldo": _format_number(_attr(tri, "saldo")),
                        "cuota": _format_number(_attr(tri, "cuota")),
                        "porcentajeUso": _format_number(_attr(tri, "porcentajeUso")),
                        "calificacion": _map_calificacion(_attr(tri, "calificacion")),
                        "moraMaxima": _attr(tri, "moraMaxima"),
                        "mesesMoraMaxima": _format_number(_attr(tri, "mesesMoraMaxima")),
                        "totalAbiertas": _format_number(_attr(tri, "totalAbiertas")),
                        "totalCerradas": _format_number(_attr(tri, "totalCerradas")),
                    }
                )
            prom = evo.find("AnalisisPromedio")
            if prom is not None:
                evolucion_deuda_agregada["promedio"] = {
                    "cupoTotal": _format_number(_attr(prom, "cupoTotal")),
                    "saldo": _format_number(_attr(prom, "saldo")),
                    "cuota": _format_number(_attr(prom, "cuota")),
                    "porcentajeUso": _format_number(_attr(prom, "porcentajeUso")),
                    "calificacion": _map_calificacion(_attr(prom, "calificacion")),
                    "moraMaxima": _attr(prom, "moraMaxima"),
                }

    info_micro = informe.find("InfoAgregadaMicrocredito")
    if info_micro is not None:
        evo_micro = info_micro.find("EvolucionDeuda")
        if evo_micro is not None:
            for sector in evo_micro.findall("EvolucionDeudaSector"):
                sector_name = _attr(sector, "nombreSector") or _attr(sector, "codSector")
                tipo_rows = []
                sector_dates = []
                for tipo in sector.findall("EvolucionDeudaTipoCuenta"):
                    tris = tipo.findall("Trimestre")
                    if not tris:
                        continue
                    if not sector_dates:
                        sector_dates = [t.attrib.get("fecha", "") for t in tris]
                    values = []
                    for t in tris:
                        values.append(_format_number(_attr(t, "saldo")))
                    tipo_rows.append(
                        {
                            "tipoCuenta": _map_tipo_cuenta(_attr(tipo, "tipoCuenta")),
                            "values": values,
                        }
                    )
                if tipo_rows:
                    evolucion_deuda_micro["sectors"].append(
                        {
                            "sector": sector_name,
                            "dates": [_format_month_label(d) for d in sector_dates if d],
                            "rows": tipo_rows,
                        }
                    )

    analisis = informe.find("AnalisisVectores")
    if analisis is not None:
        for sector in analisis.findall("Sector"):
            sector_name = _attr(sector, "nombreSector") or "Sector"
            sector_dates = []
            for cuenta in sector.findall("Cuenta"):
                fechas = [c.attrib.get("fecha", "") for c in cuenta.findall("CaracterFecha")]
                if fechas:
                    sector_dates = fechas
                    break
            if not sector_dates:
                moras = sector.find("MorasMaximas")
                if moras is not None:
                    sector_dates = [c.attrib.get("fecha", "") for c in moras.findall("CaracterFecha")]

            labels = [_format_month_label(d) for d in sector_dates if d]
            rows = []
            for cuenta in sector.findall("Cuenta"):
                cf_map = {}
                for c in cuenta.findall("CaracterFecha"):
                    date_key = c.attrib.get("fecha", "")
                    cf_map[date_key] = _attr(c, "saldoDeudaTotalMora") or "-"
                values = [cf_map.get(d, "-") for d in sector_dates]
                rows.append(
                    {
                        "entidad": _attr(cuenta, "entidad"),
                        "numeroCuenta": _attr(cuenta, "numeroCuenta"),
                        "tipoCuenta": _attr(cuenta, "tipoCuenta"),
                        "estado": _attr(cuenta, "estado"),
                        "values": values,
                        "is_total": False,
                    }
                )

            moras = sector.find("MorasMaximas")
            if moras is not None:
                mm_map = {}
                for c in moras.findall("CaracterFecha"):
                    date_key = c.attrib.get("fecha", "")
                    mm_map[date_key] = _attr(c, "saldoDeudaTotalMora") or "-"
                mm_values = [mm_map.get(d, "-") for d in sector_dates]
                rows.append(
                    {
                        "entidad": f"Moras Máximas {sector_name}",
                        "numeroCuenta": "",
                        "tipoCuenta": "",
                        "estado": "",
                        "values": mm_values,
                        "is_total": True,
                    }
                )

            if labels and rows:
                analisis_vectores["sectors"].append(
                    {
                        "sector": sector_name,
                        "dates": labels,
                        "rows": rows,
                    }
                )

    cuentas_ahorro = []
    for cuenta in informe.findall("CuentaAhorro"):
        valor = cuenta.find("Valores/Valor")
        cuentas_ahorro.append(
            {
                "entidad": _attr(cuenta, "entidad"),
                "numero": _attr(cuenta, "numero"),
                "fechaApertura": _format_date_compact(_attr(cuenta, "fechaApertura")),
                "estado": _attr(cuenta, "estado"),
                "calificacion": _map_calificacion(_attr(cuenta, "calificacion")),
                "situacionTitular": _map_situacion_titular(_attr(cuenta, "situacionTitular")),
                "ciudad": _attr(cuenta, "ciudad"),
                "saldoActual": _format_number(_attr(valor, "saldoActual")),
                "saldoMora": _format_number(_attr(valor, "saldoMora")),
            }
        )

    tarjetas = []
    for cuenta in informe.findall("TarjetaCredito"):
        valor = cuenta.find("Valores/Valor")
        comportamiento = _wrap_behavior(_attr(cuenta, "comportamiento"))
        tarjetas.append(
            {
                "entidad": _attr(cuenta, "entidad"),
                "numero": _attr(cuenta, "numero"),
                "fechaApertura": _format_date_compact(_attr(cuenta, "fechaApertura")),
                "fechaVencimiento": _format_date_compact(_attr(cuenta, "fechaVencimiento")),
                "estado": _attr(cuenta, "estado"),
                "calificacion": _map_calificacion(_attr(cuenta, "calificacion")),
                "situacionTitular": _map_situacion_titular(_attr(cuenta, "situacionTitular")),
                "garante": _map_garante(_attr(cuenta.find("Caracteristicas"), "codigoAmparada")),
                "comportamiento": comportamiento,
                "saldoActual": _format_number(_attr(valor, "saldoActual")),
                "saldoMora": _format_number(_attr(valor, "saldoMora")),
                "cuota": _format_number(_attr(valor, "cuota")),
            }
        )

    carteras = []
    for cuenta in informe.findall("CuentaCartera"):
        valor = cuenta.find("Valores/Valor")
        comportamiento = _wrap_behavior(_attr(cuenta, "comportamiento"))
        carteras.append(
            {
                "entidad": _attr(cuenta, "entidad"),
                "numero": _attr(cuenta, "numero"),
                "fechaApertura": _format_date_compact(_attr(cuenta, "fechaApertura")),
                "fechaVencimiento": _format_date_compact(_attr(cuenta, "fechaVencimiento")),
                "tipoCuenta": _attr(cuenta.find("Caracteristicas"), "tipoCuenta"),
                "calificacion": _map_calificacion(_attr(cuenta, "calificacion")),
                "garante": _map_garante(_attr(cuenta.find("Caracteristicas"), "calidadDeudor")),
                "situacionTitular": _map_situacion_titular(_attr(cuenta, "situacionTitular")),
                "comportamiento": comportamiento,
                "saldoActual": _format_number(_attr(valor, "saldoActual")),
                "saldoMora": _format_number(_attr(valor, "saldoMora")),
                "cuota": _format_number(_attr(valor, "cuota")),
            }
        )

    consultas = []
    for consulta in informe.findall("Consulta"):
        consultas.append(
            {
                "fecha": _attr(consulta, "fecha"),
                "tipoCuenta": _attr(consulta, "tipoCuenta"),
                "entidad": _attr(consulta, "entidad"),
                "razon": _attr(consulta, "razon"),
                "cantidad": _attr(consulta, "cantidad"),
                "nitSuscriptor": _attr(consulta, "nitSuscriptor").lstrip("0"),
            }
        )

    fecha_consulta_raw = _attr(informe, "fechaConsulta")
    fecha_consulta_fmt = fecha_consulta_raw
    try:
        dt = datetime.fromisoformat(fecha_consulta_raw.replace("Z", ""))
        fecha_consulta_fmt = dt.strftime("%Y/%m/%d - %I.%M %p").replace("AM", "AM").replace("PM", "PM")
    except Exception:
        pass

    return {
        "meta": {
            "fechaConsulta": fecha_consulta_raw,
            "fechaConsultaFormatted": fecha_consulta_fmt,
            "respuesta": _attr(informe, "respuesta"),
            "codSeguridad": _attr(informe, "codSeguridad"),
            "tipoIdDigitado": _attr(informe, "tipoIdDigitado"),
            "identificacionDigitada": _attr(informe, "identificacionDigitada"),
            "apellidoDigitado": _attr(informe, "apellidoDigitado"),
        },
        "natural": {
            "nombreCompleto": _attr(natural, "nombreCompleto"),
            "nombres": _attr(natural, "nombres"),
            "primerApellido": _attr(natural, "primerApellido"),
            "segundoApellido": _attr(natural, "segundoApellido"),
            "genero": _attr(natural, "genero"),
            "numero": _attr(identificacion, "numero"),
            "ciudad": _attr(identificacion, "ciudad"),
            "departamento": _attr(identificacion, "departamento"),
            "fechaExpedicion": _attr(identificacion, "fechaExpedicion"),
            "edadMin": _attr(edad, "min"),
            "edadMax": _attr(edad, "max"),
        },
        "scores": scores,
        "perfil_general": perfil_general_rows,
        "saldos_moras": saldos_moras,
        "saldos_moras_matrix": saldos_moras_matrix,
        "endeudamiento_actual": endeudamiento_rows,
        "endeudamiento_grouped": endeudamiento_grouped,
        "endeudamiento_totals": {
            "valorInicial": _format_number(str(endeudamiento_totals["valorInicial"])),
            "saldoActual": _format_number(str(endeudamiento_totals["saldoActual"])),
            "saldoMora": _format_number(str(endeudamiento_totals["saldoMora"])),
            "cuotaMes": _format_number(str(endeudamiento_totals["cuotaMes"])),
        },
        "tendencia_matrix": tendencia_matrix,
        "analisis_vectores": analisis_vectores,

        "cheques": cheques,
        "composicion_portafolio": composicion_portafolio,
        "resumen_endeudamiento": resumen_endeudamiento,
        "historico_saldos": historico_saldos,
        "evolucion_deuda_agregada": evolucion_deuda_agregada,
        "evolucion_deuda_micro": evolucion_deuda_micro,
        "cuentas_ahorro": cuentas_ahorro,
        "tarjetas": tarjetas,
        "carteras": carteras,
        "consultas": consultas,
    }


def _fill_dashes(data):
    if isinstance(data, dict):
        return {k: _fill_dashes(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_fill_dashes(v) for v in data]
    if data is None:
        return "-"
    if isinstance(data, str) and data.strip() == "":
        return "-"
    return data


def xml_to_rows(xml_str: str) -> list[tuple[str, str]]:
    root = _parse_root(xml_str)

    rows: list[tuple[str, str]] = [("Path", "Value")]

    def walk(elem: ET.Element, path: str) -> None:
        for key, value in elem.attrib.items():
            rows.append((f"{path}.@{key}", str(value)))

        text = (elem.text or "").strip()
        if text:
            rows.append((path, text))

        for child in list(elem):
            child_tag = _strip_ns(child.tag)
            walk(child, f"{path}/{child_tag}")

    walk(root, _strip_ns(root.tag))
    return rows


def _logo_data_uri() -> str:
    logo_path = Path(settings.BASE_DIR) / "static" / "img" / "LogoHD.png"
    if not logo_path.exists():
        return ""
    data = logo_path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _render_html(xml_str: str) -> str:
    context = _fill_dashes(_parse_xml(xml_str))
    context["logo_data_uri"] = _logo_data_uri()
    context["generated_at"] = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    return render_to_string("integrations/hdcplus_pdf.html", context)


def xml_to_pdf_bytes(xml_str: str) -> bytes:
    try:
        from weasyprint import HTML
    except Exception as exc:  # pragma: no cover
        raise DatacreditoReportError(
            "WeasyPrint no esta instalado. Instala con: pip install weasyprint"
        ) from exc

    html = _render_html(xml_str)
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()

# Alineación Datacrédito Web vs API Experian

## A) Nuevo esquema / estructura objetivo (alineado al PDF web)

La salida del parser ahora incluye un DTO `legacy_report` con esta jerarquía:

```json
{
  "informacionBasica": { ... },
  "detalleSocioDemografico": [ ... ],
  "habitoPagoAbiertasVigentes": [
    {
      "sector": "Sector Financiero|Cooperativo|Real|Telcos",
      "rows": [
        {
          "entidadInformante": "...",
          "tipoCuenta": "...",
          "numeroCuenta": "...",
          "calificacion": "...",
          "estadoObligacion": "...",
          "fechaActualizacion": "YYYYMMDD",
          "adjetivoFecha": "...",
          "fechaApertura": "YYYYMMDD",
          "fechaVencimiento": "YYYYMMDD|-",
          "moraMaxima": "30|60|90|120|-",
          "mesesHistorial": "...",
          "comportamiento": "[....][....]",
          "desacuerdoInformacion": "...",
          "estadoTitular": "...",
          "marcaClase": "...",
          "tipoGarantia": "...",
          "valorCupoInicial": "...",
          "saldoActual": "...",
          "saldoMora": "...",
          "valorCuota": "...",
          "fechaLimitePago": "YYYYMMDD|-",
          "fechaPago": "YYYYMMDD|-",
          "permanencia": "...",
          "chequesDevueltos": "...",
          "cuotasMVigencia": "...",
          "porcentajeDeuda": "...",
          "oficinaDeudor": "...",
          "condicion": "Vigente|Cerrada|-",
          "fechaCierre": "YYYYMMDD|-"
        }
      ]
    }
  ],
  "habitoPagoCerradasInactivas": [ ... ],
  "habitoPagoSinClasificar": [ ... ],
  "camposNoDisponiblesApi": [ ... ]
}
```

Además de `legacy_report`, se exponen alias para template:
- `legacy_info_basica`
- `legacy_detalle_sociodemografico`
- `legacy_habito_abiertas`
- `legacy_habito_cerradas`
- `legacy_habito_otras`
- `legacy_campos_no_disponibles_api`

## B) Mapeo campo a campo (origen API -> modelo -> presentación)

> Fuente base inspeccionada: `tmp_sample_credit.xml` y nodos XML guardados en `CreditReportQuery.raw_xml`.

| Sección | Campo PDF web | Origen API (XPath) | Destino modelo | Presentación final |
|---|---|---|---|---|
| Info básica | Tipo Documento | `/Informes/Informe/@tipoIdDigitado` | `legacy_info_basica.tipoDocumento` | `C.C.`, `NIT`, etc. |
| Info básica | Número Documento | `/Informes/Informe/@identificacionDigitada` | `legacy_info_basica.numeroDocumento` | Texto |
| Info básica | Estado Documento | `/Informes/Informe/NaturalNacional/Identificacion/@estado` | `legacy_info_basica.estadoDocumento` | `Vigente` etc. |
| Info básica | Lugar Expedición | `/Informes/Informe/NaturalNacional/Identificacion/@ciudad` | `legacy_info_basica.lugarExpedicion` | Texto |
| Info básica | Fecha Expedición | `/Informes/Informe/NaturalNacional/Identificacion/@fechaExpedicion` | `legacy_info_basica.fechaExpedicion` | `DD/MM/YYYY` |
| Info básica | Nombre | `/Informes/Informe/NaturalNacional/@nombreCompleto` | `legacy_info_basica.nombre` | Texto |
| Info básica | Rango Edad | `/Informes/Informe/NaturalNacional/Edad/@min,@max` | `legacy_info_basica.rangoEdad` | `min-max` |
| Info básica | Género | `/Informes/Informe/NaturalNacional/@genero` | `legacy_info_basica.genero` | `Masculino/Femenino` |
| Info básica | Tiene RUT | `/Informes/Informe/NaturalNacional/@rut` | `legacy_info_basica.tieneRut` | `SI/NO` |
| Info básica | Actividad Económica | `/Informes/Informe/NaturalNacional/@actividadEconomica` (si existe) | `legacy_info_basica.actividadEconomica` | Texto o `-` |
| Info básica | Empleador | `/Informes/Informe/NaturalNacional/InfoDemografica/*/@razonSocial` | `legacy_info_basica.empleador` | Texto o `-` |
| Info básica | Tipo Contrato | `/Informes/Informe/NaturalNacional/@tipoContrato` o `/InfoDemografica/Contrato/@tipo` | `legacy_info_basica.tipoContrato` | Texto o `-` |
| Info básica | Fecha Contrato | `/Informes/Informe/NaturalNacional/@fechaContrato` o `/InfoDemografica/Contrato/@fecha` | `legacy_info_basica.fechaContrato` | `DD/MM/YYYY` o `-` |
| Info básica | Opera Internacionales | `/Informes/Informe/NaturalNacional/InfoDemografica/OperacionesInternacionales/@operaInt` | `legacy_info_basica.operacionesInternacionales` | `SI/NO/-` |
| Socio demo | Reportado por | `/InfoDemografica/OperacionesInternacionales/@razonSocial`, `/InfoDemografica/Identificacion/@razonSocial` | `legacy_detalle_sociodemografico[].reportadoPor` | Texto |
| Socio demo | NIT reporta | `/InfoDemografica/*/@nitReporta` | `legacy_detalle_sociodemografico[].nitReporta` | Texto |
| Socio demo | Fecha reporte | `/InfoDemografica/OperacionesInternacionales/@fecha`, `/InfoDemografica/Identificacion/@fechaExpedicion` | `legacy_detalle_sociodemografico[].fechaReporte` | Fecha |
| Hábito (ambas) | Entidad informante | `/(CuentaAhorro|CuentaCorriente|TarjetaCredito|CuentaCartera)/@entidad` | `rows[].entidadInformante` | Texto |
| Hábito (ambas) | Tipo cuenta | `CuentaCartera/Caracteristicas/@tipoCuenta`, `TarjetaCredito=>TDC`, `Ahorro=>AHO/AHD` | `rows[].tipoCuenta` | Abrev. |
| Hábito (ambas) | Num cta 9 dígitos | `/(...)/@numero` | `rows[].numeroCuenta` | Texto |
| Hábito (ambas) | Calificación | `/Valores/Valor/@calificacion` (fallback `@calificacion`) | `rows[].calificacion` | `A,B,C...` |
| Hábito (ambas) | Estado obligación | `/Estados/(EstadoPago|EstadoCuenta|EstadoOrigen|EstadoPlastico)` + `@formaPago` | `rows[].estadoObligacion` | Multilínea (`+ Al dia`, `Orig`, `Plastico`) |
| Hábito (ambas) | Fecha actualización | `/Valores/Valor/@fecha` (fallback estado) | `rows[].fechaActualizacion` | `YYYYMMDD` |
| Hábito (ambas) | Fecha apertura | `/(...)/@fechaApertura` | `rows[].fechaApertura` | `YYYYMMDD` |
| Hábito (ambas) | Fecha vencimiento | `/(TarjetaCredito|CuentaCartera)/@fechaVencimiento` | `rows[].fechaVencimiento` | `YYYYMMDD` o `-` |
| Hábito (ambas) | Mora máxima | `/Estados/EstadoPago/@codigo` | `rows[].moraMaxima` | `30/60/90/120/-` |
| Hábito (ambas) | Historial comportamiento | `/(TarjetaCredito|CuentaCartera)/@comportamiento` | `rows[].comportamiento` | Bloques `[....][....]` |
| Hábito (ambas) | Estado titular | `/(...)/@situacionTitular` | `rows[].estadoTitular` | Tabla 28 |
| Hábito (ambas) | Marca/Clase | `/Caracteristicas/@marca,@clase` | `rows[].marcaClase` | Texto |
| Hábito (ambas) | Tipo garantía | `/Caracteristicas/@garantia` | `rows[].tipoGarantia` | `ADMIS/NO IDONEA/...` |
| Hábito (ambas) | Vlr/cupo inicial | `/Valores/Valor/@valorInicial` o `@cupoTotal` en TDC | `rows[].valorCupoInicial` | Miles |
| Hábito (ambas) | Saldo actual | `/Valores/Valor/@saldoActual` | `rows[].saldoActual` | Miles |
| Hábito (ambas) | Saldo mora | `/Valores/Valor/@saldoMora` | `rows[].saldoMora` | Miles |
| Hábito (ambas) | Valor cuota | `/Valores/Valor/@cuota` | `rows[].valorCuota` | Miles |
| Hábito (ambas) | Fecha límite pago | `/Valores/Valor/@fechaLimitePago` | `rows[].fechaLimitePago` | `YYYYMMDD` |
| Hábito (ambas) | Fecha del pago | `/Valores/Valor/@fechaPagoCuota` | `rows[].fechaPago` | `YYYYMMDD` |
| Hábito (ambas) | Permanencia | `/Caracteristicas/@mesesPermanencia` | `rows[].permanencia` | Texto |
| Hábito (ambas) | Cuotas/M/Vigencia | `/Valores/Valor/@cuotasCanceladas,@totalCuotas,@periodicidad` | `rows[].cuotasMVigencia` | `x de y/M/D` |
| Hábito (ambas) | % deuda | cálculo (`saldoActual/base`) | `rows[].porcentajeDeuda` | `%` |
| Hábito (ambas) | Oficina/Deudor | `/(...)/@oficina` + `/Caracteristicas/@calidadDeudor` o `@codigoAmparada` | `rows[].oficinaDeudor` | `Oficina / Rol` |
| Hábito (cerradas) | Fecha cierre | `/Estados/EstadoCuenta/@fecha` (fallback) | `rows[].fechaCierre` | `YYYYMMDD` |

## C) Campos del PDF web que no se pueden obtener por API (según XML inspeccionado)

1. **Antigüedad Ubicación**
   - No existe nodo explícito en `NaturalNacional` ni en `InfoDemografica` en XML HC2 observado.
   - Se deja `-`.

2. **Desacuerdo con la información por obligación**
   - En XML existe a nivel agregado (`PerfilGeneral/Desacuerdos`), pero no por cuenta/obligación.
   - Se deja `-` por fila de hábito.

3. **Campos laborales detallados por obligación (actividad, empleador, tipo/fecha contrato por producto)**
   - No aparecen por cuenta en nodos `CuentaAhorro/TarjetaCredito/CuentaCartera`.
   - Solo puede inferirse parcialmente desde `InfoDemografica`.

## D) Ejemplo serializado final

Se dejó ejemplo real en:

- `docs/legacy_report_sample.json`

Generado desde `tmp_sample_credit.xml`, con la nueva jerarquía de Datacrédito web.

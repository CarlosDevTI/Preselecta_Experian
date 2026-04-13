# Preselecta Experian - Integracion Congente

Proyecto interno para integrar **Preselecta (Experian)** y **Historia de Credito+** en un flujo de consulta con OTP, auditoria y generacion de PDF.

## Funcionalidad principal

- Consulta Preselecta con validacion de decision (Aprobado / Zona Gris).
- Envio y validacion de OTP (SMS por Twilio Verify, Email por OTP interno).
- Generacion de consentimiento legal en PDF (AcroForm).
- Consulta de Historia de Credito+ (SOAP) y generacion de PDF.
- Auditoria del flujo (eventos, PDFs y metadatos).

## Flujo resumido

1. Consulta Preselecta.
2. Si la decision es **Aprobado** o **Zona Gris**, se habilita Historial de Pago.
3. Se solicita OTP.
4. OTP aprobado:
   - Se genera PDF de consentimiento.
   - Se consulta historial (SOAP -> XML -> PDF).
   - Se guarda evidencia para auditoria.

## Estructura

- `integrations/`
  - Vistas principales (Preselecta, OTP, auditoria).
  - Templates de front y PDF.
  - Servicios de integracion.
- `api/`
  - Endpoints de consulta SOAP.
  - Generacion de PDF de Historia de Credito+.
- `static/`
  - Assets (logos, etc.).
- `certs/`
  - Certificados de cliente (no se versiona).

## Requisitos

- Python 3.12+
- Docker + Docker Compose
- PostgreSQL (produccion)
- SQLite (desarrollo local)

## Configuracion

Completa `.env` con credenciales:
- Preselecta (OKTA, token, URL)
- Historia de Credito+ (SOAP, certs, credenciales)
- Twilio Verify
- SMTP para OTP Email
- Base de datos

## Instalacion local

```bash
python -m venv venv
source venv/Scripts/activate  # Windows
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Docker

```bash
docker compose down
docker compose up -d --build
```

## Auditoria

Ruta:

```
/preselecta/admin-auditoria/
```

## Seguridad

- No versionar certificados (`certs/` esta en `.gitignore`).
- Mantener credenciales solo en `.env`.
- Evitar exponer OTP en logs.

## Notas

Proyecto de uso interno para Congente.

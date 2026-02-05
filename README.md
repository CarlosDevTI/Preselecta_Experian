# Preselecta Experian - Integración Congente

Proyecto interno para integrar **Preselecta (Experian)** y **Historia de Crédito+** en un flujo de consulta con OTP, auditoría y generación de PDF.

## Funcionalidad principal

- Consulta Preselecta con validación de decisión (Aprobado / Zona Gris).
- Envío y validación de OTP (Twilio Verify).
- Generación de consentimiento legal en PDF (AcroForm).
- Consulta de Historia de Crédito+ (SOAP) y generación de PDF.
- Auditoría completa del flujo (eventos, PDFs y metadatos).

## Flujo resumido

1. Se realiza la consulta Preselecta.
2. Si la decisión es **Aprobado** o **Zona Gris**, se habilita el botón de **Historial de Pago**.
3. Al hacer clic, se solicita OTP.
4. OTP aprobado:
   - Se genera el PDF de consentimiento.
   - Se consulta el historial (SOAP → XML → PDF).
   - Se guarda todo para auditoría.

## Estructura del proyecto

- `integrations/`
  - Vistas principales (Preselecta, OTP, auditoría).
  - Templates de front y PDF.
  - Servicios de integración.
- `api/`
  - Endpoints de consulta SOAP.
  - Generación de PDF de Historia de Crédito+.
- `static/`
  - Assets (logos, etc.).
- `certs/`
  - Certificados de cliente (NO se versiona).

## Requisitos

- Python 3.12+
- Docker + Docker Compose
- PostgreSQL (producción)
- SQLite (desarrollo local)

## Configuración

Copiar el `.env` y completar con credenciales:

- Preselecta (OKTA, token, URL)
- Historia de Crédito+ (SOAP, certs, credenciales)
- Twilio Verify
- Base de datos

## Instalación local (desarrollo)

```bash
python -m venv venv
source venv/Scripts/activate  # Windows
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Docker (producción o staging)

```bash
docker compose down
docker compose up -d --build
```

## Auditoría

Vista custom:

```
/preselecta/admin-auditoria/
```

Permite consultar:

- Nombre / Documento
- Resultado Preselecta
- OTP (estado y fecha)
- PDFs generados (consentimiento e historial)

## Seguridad

- **No versionar certificados** (`certs/` está en `.gitignore`).
- Mantener credenciales solo en `.env`.
- Evitar exponer OTP en logs.

## Notas

Este proyecto es de uso **interno** para Congente.  
Si necesitas cambios o soporte, contactar a Gerencia TI.

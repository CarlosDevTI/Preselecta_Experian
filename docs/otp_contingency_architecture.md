# OTP Contingencia - Arquitectura Recomendada

## 1. Arquitectura implementada (fase actual)
- Patron: modulo interno desacoplado (`integrations/services/otp_service.py`).
- Canal primario: SMS via Twilio Verify.
- Fallback: OTP por email SMTP con codigo generado internamente.
- Persistencia:
  - `ConsentOTP`: estado funcional del flujo de consentimiento.
  - `OTPChallenge`: desafio OTP por canal/proveedor (expiracion, intentos, resultado).
  - `OTPAuditLog`: bitacora de eventos para trazabilidad legal.

## 2. Seguridad aplicada
- OTP email generado con `secrets` (criptograficamente fuerte).
- OTP email almacenado solo como hash (`make_password` / `check_password`).
- No se guarda OTP plano en BD.
- OTP invalidado automaticamente al validar con exito.
- Limite de intentos por desafio (`max_attempts`, `attempts_used`).
- Expiracion configurable por canal.
- Captura de metadatos: `session_key`, IP, XFF, user-agent, contexto.

## 3. Politica de fallback
- Primario siempre SMS.
- Contingencia email habilitada automaticamente cuando:
  - OTP SMS falla validacion, o
  - OTP SMS expira, o
  - se supera timeout de fallback configurable.
- Activacion de fallback queda auditada con `fallback_reason`.

## 4. Trazabilidad y cumplimiento
- Auditoria por evento (`generated`, `sent`, `validated_ok`, `validated_fail`, `fallback_enabled`, `fallback_used`, `invalidated`).
- En consentimiento PDF se imprime huella legal:
  - `Documento autorizado mediante OTP ****** via SMS/EMAIL`
- Nunca se expone OTP completo en reportes ni en PDF.

## 5. Roadmap recomendado (fase financiera robusta)
- Multi-provider SMS:
  - Interfaz unica `SmsProvider` con adaptadores (`Twilio`, `ProviderB`).
  - Estrategia de failover por error tecnico (connection/timeouts/5xx).
- Inmutabilidad de logs:
  - Replicar `OTPAuditLog` a almacenamiento WORM/SIEM.
  - Firmar hash diario de eventos (integridad forense).
- Rate limiting:
  - Limite por usuario, persona consultada, IP, y ventana temporal.
- Segregacion de responsabilidades:
  - OTP service externo (microservicio) cuando escale volumen/regulatorio.

## 6. Decision arquitectonica recomendada
- Corto plazo (actual): modulo interno desacoplado en monolito (menor friccion operativa, despliegue simple).
- Mediano plazo: extraer OTP como microservicio cuando haya:
  - necesidad de alta disponibilidad independiente,
  - multiples canales/proveedores,
  - requisitos de auditoria centralizada cross-sistema.

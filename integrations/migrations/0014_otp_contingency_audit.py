from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0013_useraccessprofile_and_audit_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="consentotp",
            name="authorized_channel",
            field=models.CharField(blank=True, max_length=10),
        ),
        migrations.AddField(
            model_name="consentotp",
            name="authorized_otp_masked",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name="consentotp",
            name="email_address",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="consentotp",
            name="fallback_reason",
            field=models.TextField(blank=True),
        ),
        migrations.CreateModel(
            name="OTPChallenge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("channel", models.CharField(choices=[("sms", "SMS"), ("email", "Email")], max_length=10)),
                (
                    "provider",
                    models.CharField(
                        choices=[("twilio_verify", "Twilio Verify"), ("internal_email", "Internal Email OTP")],
                        max_length=30,
                    ),
                ),
                ("destination", models.CharField(max_length=255)),
                ("destination_masked", models.CharField(blank=True, max_length=255)),
                ("otp_hash", models.TextField(blank=True)),
                ("otp_masked", models.CharField(blank=True, max_length=20)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("verified", "Verified"),
                            ("expired", "Expired"),
                            ("failed", "Failed"),
                            ("canceled", "Canceled"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("generated_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("max_attempts", models.PositiveSmallIntegerField(default=5)),
                ("attempts_used", models.PositiveSmallIntegerField(default=0)),
                ("validation_result", models.CharField(blank=True, max_length=40)),
                ("fallback_reason", models.TextField(blank=True)),
                ("twilio_verification_sid", models.CharField(blank=True, max_length=64)),
                ("twilio_check_sid", models.CharField(blank=True, max_length=64)),
                ("session_key", models.CharField(blank=True, max_length=120)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("forwarded_for", models.TextField(blank=True)),
                ("user_agent", models.TextField(blank=True)),
                ("context", models.JSONField(blank=True, default=dict)),
                ("last_error", models.TextField(blank=True)),
                (
                    "consent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="otp_challenges",
                        to="integrations.consentotp",
                    ),
                ),
            ],
            options={"ordering": ["-generated_at"]},
        ),
        migrations.CreateModel(
            name="OTPAuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("generated", "Generated"),
                            ("sent", "Sent"),
                            ("validated_ok", "Validated OK"),
                            ("validated_fail", "Validated Fail"),
                            ("fallback_enabled", "Fallback Enabled"),
                            ("fallback_used", "Fallback Used"),
                            ("invalidated", "Invalidated"),
                        ],
                        max_length=40,
                    ),
                ),
                ("channel", models.CharField(blank=True, max_length=10)),
                ("provider", models.CharField(blank=True, max_length=30)),
                ("otp_hash_snapshot", models.TextField(blank=True)),
                ("result", models.CharField(blank=True, max_length=40)),
                ("reason", models.TextField(blank=True)),
                ("session_key", models.CharField(blank=True, max_length=120)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("forwarded_for", models.TextField(blank=True)),
                ("user_agent", models.TextField(blank=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "challenge",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="audit_logs",
                        to="integrations.otpchallenge",
                    ),
                ),
                (
                    "consent",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="otp_audit_logs",
                        to="integrations.consentotp",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0004_remove_accesslog_real_ip_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConsentOTP",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("phone_number", models.CharField(max_length=20)),
                ("channel", models.CharField(default="sms", max_length=10)),
                ("status", models.CharField(default="pending", max_length=20)),
                ("verify_service_sid", models.CharField(blank=True, max_length=64)),
                ("verification_sid", models.CharField(blank=True, max_length=64)),
                ("verification_check_sid", models.CharField(blank=True, max_length=64)),
                ("id_number", models.CharField(blank=True, max_length=50)),
                ("id_type", models.CharField(blank=True, max_length=10)),
                ("first_last_name", models.CharField(blank=True, max_length=200)),
                ("request_payload", models.JSONField()),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("forwarded_for", models.TextField(blank=True, help_text="Cabecera X-Forwarded-For completa")),
                ("user_agent", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("verified_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]

from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="CreditReportQuery",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_uuid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("provider", models.CharField(choices=[("DATACREDITO", "DataCredito")], max_length=50)),
                ("service_name", models.CharField(blank=True, max_length=80)),
                ("operation", models.CharField(blank=True, max_length=80)),
                ("person_id_type", models.CharField(max_length=10)),
                ("person_id_number", models.CharField(max_length=40)),
                ("person_last_name", models.CharField(max_length=120)),
                ("product_id", models.CharField(blank=True, max_length=20)),
                ("info_account_type", models.CharField(blank=True, max_length=20)),
                ("codes_value", models.CharField(blank=True, max_length=50)),
                ("originator_channel_name", models.CharField(blank=True, max_length=120)),
                ("originator_channel_type", models.CharField(blank=True, max_length=40)),
                ("requested_by", models.CharField(blank=True, max_length=120)),
                ("requester_ip", models.GenericIPAddressField(blank=True, null=True)),
                ("status", models.CharField(default="PENDING", max_length=20)),
                ("http_status", models.IntegerField(blank=True, null=True)),
                ("error_code", models.CharField(blank=True, max_length=100)),
                ("error_message", models.TextField(blank=True)),
                ("soap_request_xml", models.TextField(blank=True)),
                ("soap_response_xml", models.TextField(blank=True)),
                ("pdf_file", models.FileField(blank=True, null=True, upload_to="datacredito_reports/")),
                ("pdf_sha256", models.CharField(blank=True, max_length=64)),
                ("consulted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="creditreportquery",
            index=models.Index(fields=["provider", "person_id_type", "person_id_number"], name="api_creditr_provider_f470ce_idx"),
        ),
    ]

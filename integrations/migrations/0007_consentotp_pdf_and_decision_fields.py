from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0006_consentotp_public_id_and_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="consentotp",
            name="decision",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="consentotp",
            name="risk_level",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="consentotp",
            name="consent_pdf",
            field=models.FileField(blank=True, null=True, upload_to="consents/"),
        ),
    ]

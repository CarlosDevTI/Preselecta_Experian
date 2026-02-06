from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("integrations", "0010_alter_consentotp_consent_pdf"),
    ]

    operations = [
        migrations.AddField(
            model_name="consentotp",
            name="last_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="consentotp",
            name="resend_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]

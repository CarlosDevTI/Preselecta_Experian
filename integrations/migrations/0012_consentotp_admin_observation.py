from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0011_consentotp_resend_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="consentotp",
            name="admin_observation",
            field=models.TextField(blank=True, default=""),
        ),
    ]

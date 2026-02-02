from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("integrations", "0005_consentotp"),
    ]

    operations = [
        migrations.AddField(
            model_name="consentotp",
            name="public_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="consentotp",
            name="full_name",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="consentotp",
            name="place",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]

from rest_framework import serializers


class HDCPlusSOAPQuerySerializer(serializers.Serializer):
    person_id_number = serializers.CharField()
    person_id_type = serializers.CharField()
    person_last_name = serializers.CharField()
    originator_channel_name = serializers.CharField(default="Canal XYZ", required=False)
    originator_channel_type = serializers.CharField(default="42", required=False)
    codes_value = serializers.CharField(default="", required=False, allow_blank=True)


class HC2SoapNaturalSerializer(serializers.Serializer):
    person_id_number = serializers.CharField()
    person_id_type = serializers.CharField()
    person_last_name = serializers.CharField()
    codes_value = serializers.CharField(default="", required=False, allow_blank=True)
    celebrity_id = serializers.CharField(default="1", allow_blank=True, required=False)


class HC2SoapJuridicaSerializer(serializers.Serializer):
    person_id_number = serializers.CharField()
    person_id_type = serializers.CharField()
    razon_social = serializers.CharField()
    codes_value = serializers.CharField(default="", required=False, allow_blank=True)
    celebrity_id = serializers.CharField(default="1", allow_blank=True, required=False)

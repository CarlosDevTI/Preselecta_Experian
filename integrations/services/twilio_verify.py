import os
from twilio.rest import Client


class TwilioVerifyClient:
    def __init__(self):
        self.account_sid = os.environ["TWILIO_ACCOUNT_SID"]
        self.auth_token = os.environ["TWILIO_AUTH_TOKEN"]
        self.verify_sid = os.environ["TWILIO_VERIFY_SID"]
        self.client = Client(self.account_sid, self.auth_token)

    def start_verification(
        self,
        to_number: str,
        channel: str = "sms",
        template_sid: str | None = None,
        ttl_seconds: int | None = None,
    ):
        params = {"to": to_number, "channel": channel}
        if template_sid:
            params["template_sid"] = template_sid
        if ttl_seconds:
            params["ttl"] = int(ttl_seconds)
        return self.client.verify.v2.services(self.verify_sid).verifications.create(**params)

    def check_verification(self, to_number: str, code: str):
        return (
            self.client.verify.v2.services(self.verify_sid)
            .verification_checks.create(to=to_number, code=code)
        )

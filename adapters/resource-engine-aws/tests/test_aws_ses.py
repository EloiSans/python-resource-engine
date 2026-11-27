"""SesEmailSender against the AWS Emulator: verified senders deliver, others
are refused for good (RE-E502)."""

from __future__ import annotations

import uuid

import pytest
from aiobotocore.config import AioConfig
from aiobotocore.session import get_session
from resource_engine.email import Mailer, attachment
from resource_engine.errors import EmailRejected, RemoteCallFailed
from resource_engine.ports.email import EmailMessage
from resource_engine.templates import StringTemplates
from resource_engine_aws import SesEmailSender

from .conftest import CA, ENDPOINT


@pytest.fixture
async def ses():
    async with get_session().create_client(
        "sesv2",
        endpoint_url=ENDPOINT,
        region_name="us-east-1",
        aws_access_key_id="emulator",
        aws_secret_access_key="emulator",
        verify=CA,
        config=AioConfig(inject_host_prefix=False),
    ) as client:
        yield client


@pytest.mark.emulator("aws")
async def test_an_unverified_sender_is_refused_for_good(ses):
    """The emulator keeps new identities pending (no way to verify them), so
    it shows SES's refusal; delivery is checked with a recording client below."""
    domain = f"{uuid.uuid4().hex[:8]}.example.com"
    await ses.create_email_identity(EmailIdentity=domain)  # pending verification
    templates = StringTemplates({"email/hi.subject.txt": "Hola $name", "email/hi.txt": "Hola"})
    mailer = Mailer(SesEmailSender(ses), templates, from_address=f"Factures <factures@{domain}>")
    with pytest.raises(EmailRejected) as info:
        await mailer.send("hi", ["ada@example.com"], {"name": "Ada"})
    assert "MessageRejected" in info.value.details["reason"]


async def test_the_request_carries_the_mime_document():
    seen = {}

    class Client:
        async def send_email(self, **request):
            seen.update(request)
            return {"MessageId": "ses-1"}

    templates = StringTemplates({"email/hi.subject.txt": "Hola $name", "email/hi.txt": "Hola"})
    sender = SesEmailSender(Client(), configuration_set="transactional")
    mailer = Mailer(sender, templates, from_address="Factures <factures@example.com>")
    provider_id = await mailer.send(
        "hi",
        ["ada@example.com"],
        {"name": "Ada"},
        bcc=["hidden@example.com"],
        attachments=[attachment("a.pdf", b"%PDF")],
        message_id="<1@example.com>",
    )
    assert provider_id == "ses-1"
    assert seen["FromEmailAddress"] == "factures@example.com"
    assert seen["Destination"]["BccAddresses"] == ["hidden@example.com"]
    assert seen["ConfigurationSetName"] == "transactional"
    raw = seen["Content"]["Raw"]["Data"]
    assert b"Message-ID: <1@example.com>" in raw and b"Bcc" not in raw
    assert b"From: Factures <factures@example.com>" in raw and b'filename="a.pdf"' in raw


async def test_throttling_is_retried():
    from botocore.exceptions import ClientError

    class Throttled:
        async def send_email(self, **request):
            assert request["EmailTags"] == [{"Name": "template", "Value": "hi"}]
            raise ClientError(
                {"Error": {"Code": "TooManyRequestsException", "Message": "slow"}}, "SendEmail"
            )

    message = EmailMessage(
        sender="a@x.com", to=["b@x.com"], subject="x", text="x", tags={"template": "hi"}
    )
    with pytest.raises(RemoteCallFailed):
        await SesEmailSender(Throttled()).send(message)

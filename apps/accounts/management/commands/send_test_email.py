"""Operational diagnostic: send a single test email to verify the configured
EMAIL_BACKEND actually delivers. Use from Render Shell after wiring up
RESEND_API_KEY + domain verification.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Send a one-shot test email to verify the email backend is wired up."

    def add_arguments(self, parser):
        parser.add_argument("to", help="Recipient email address")
        parser.add_argument(
            "--subject",
            default="ekiptahmin.com — test email",
            help="Override the subject line.",
        )

    def handle(self, *args, to, subject, **options):
        self.stdout.write(f"EMAIL_BACKEND      = {settings.EMAIL_BACKEND}")
        self.stdout.write(f"DEFAULT_FROM_EMAIL = {settings.DEFAULT_FROM_EMAIL}")
        self.stdout.write(f"REPLY_TO_EMAIL     = {settings.REPLY_TO_EMAIL}")
        self.stdout.write(f"recipient          = {to}")
        self.stdout.write("---")
        msg = EmailMultiAlternatives(
            subject=subject,
            body=(
                "This is a test email from ekiptahmin.com.\n\n"
                "If you see this in your inbox, the email backend "
                "(Resend SMTP + domain DNS + Render env) is wired up "
                "correctly. If not, check Render logs for the "
                "'mail.failed' or 'mail.dropped' line."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to],
            reply_to=[settings.REPLY_TO_EMAIL],
        )
        try:
            sent = msg.send(fail_silently=False)
        except Exception as exc:
            raise CommandError(f"send raised: {exc!r}") from exc

        if "dummy" in settings.EMAIL_BACKEND.lower():
            self.stdout.write(self.style.WARNING(
                f"send_mail returned {sent} BUT backend is dummy — nothing "
                "was actually delivered. Set RESEND_API_KEY in Render env."
            ))
            return
        self.stdout.write(self.style.SUCCESS(
            f"send_mail returned {sent} — request accepted by SMTP server. "
            "Check the recipient inbox (and spam) to confirm delivery."
        ))

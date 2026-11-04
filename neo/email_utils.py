from django.core.mail import get_connection, EmailMessage

from .models import SiteConfiguration


def get_smtp_connection():
    """
    Get email connection using SMTP settings from SiteConfiguration
    """
    config = SiteConfiguration.get_solo()

    if not config.smtp_host:
        return None

    connection = get_connection(
        backend="django.core.mail.backends.smtp.EmailBackend",
        host=config.smtp_host,
        port=config.smtp_port,
        username=config.smtp_username,
        password=config.smtp_password,
        use_tls=config.smtp_use_tls,
        use_ssl=config.smtp_use_ssl,
    )

    return connection


def send_email(subject, message, recipient_list, from_email=None, html_message=None, attachments=None):
    """
    Send email using configured SMTP settings

    Args:
        subject (str): Email subject
        message (str): Plain text message
        recipient_list (list): List of recipient email addresses
        from_email (str, optional): Sender email. Uses config default if not provided
        html_message (str, optional): HTML version of the message
        attachments (list, optional): List of file paths to attach

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    config = SiteConfiguration.get_solo()

    if not config.smtp_host:
        raise ValueError("SMTP configuration is not set up in Site Configuration")

    if from_email is None:
        from_email = config.email_from

    if not from_email:
        raise ValueError("No 'from' email address configured")

    connection = get_smtp_connection()
    if not connection:
        return False

    try:
        email = EmailMessage(
            subject=subject,
            body=html_message or message,
            from_email=from_email,
            to=recipient_list,
            connection=connection,
        )

        if html_message:
            email.content_subtype = "html"

        if attachments:
            for attachment in attachments:
                email.attach_file(attachment)

        email.send()
        return True

    except Exception as e:
        print(f"Error sending email: {e}")
        return False

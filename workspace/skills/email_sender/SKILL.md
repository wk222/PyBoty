---
name: email_sender
description: Send emails via SMTP with HTML support and attachments
version: 1.0.0
author: system
enabled: true
---

# email_sender

Send emails via SMTP. Configure with environment variables: SMTP_SERVER, SMTP_PORT, SMTP_USE_SSL, SENDER_EMAIL, SENDER_PASSWORD, SENDER_NAME.

## Capabilities
- Send single or batch emails
- HTML email body support
- CC/BCC support

## System Prompt
You can send emails using the email tools. Before sending, ensure SMTP is configured via environment variables (SMTP_SERVER, SMTP_PORT, SENDER_EMAIL, SENDER_PASSWORD). Use send_email for single emails or send_batch_emails for multiple personalized emails.

## Dependencies
- N/A (uses Python stdlib smtplib)

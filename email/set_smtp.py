#!/usr/bin/env python3
"""Set up the mail account used by the Email panel. Run it yourself on the server:

    python3 /root/mail-send/set_smtp.py

It asks for the SMTP details (the password is typed hidden), checks the login against the server, and only then
writes /root/mail-send/smtp.env (chmod 600). The running mail-send service picks it up without a restart.

Common settings
  Gmail       host smtp.gmail.com   port 465  user = your Gmail address, password = an App Password
              (Google account > Security > 2-Step Verification > App passwords). From must be that Gmail address
              or an alias you added under Gmail > Settings > Accounts.
  Resend      host smtp.resend.com  port 465  user = resend, password = an API key; From on a verified domain
  Brevo       host smtp-relay.brevo.com port 587  user/password from Brevo > SMTP & API
"""
import getpass
import os
import smtplib
import ssl
import sys

CONFIG = os.environ.get("MAIL_CONFIG", "/root/mail-send/smtp.env")


def ask(label, default=""):
    v = input("%s%s: " % (label, " [%s]" % default if default else "")).strip()
    return v or default


def main():
    old = {}
    if os.path.exists(CONFIG):
        for line in open(CONFIG, encoding="utf-8"):
            if "=" in line and not line.startswith("#"):
                k, v = line.rstrip("\n").split("=", 1)
                old[k] = v
    host = ask("SMTP host", old.get("SMTP_HOST", "smtp.gmail.com"))
    port = int(ask("SMTP port (465 = SSL, 587 = STARTTLS)", old.get("SMTP_PORT", "465")))
    user = ask("SMTP user", old.get("SMTP_USER", ""))
    pw = getpass.getpass("SMTP password%s: " % (" (Enter = keep current)" if old.get("SMTP_PASS") else ""))
    pw = pw or old.get("SMTP_PASS", "")
    from_addr = ask("From address", old.get("FROM_ADDRESS", user))
    from_name = ask("From name", old.get("FROM_NAME", ""))
    reply_to = ask("Default Reply-To (optional)", old.get("REPLY_TO", ""))
    if not (host and user and pw and from_addr):
        sys.exit("host, user, password and From are required; nothing saved")

    print("Checking the login with %s:%d ..." % (host, port))
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            s = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
        else:
            s = smtplib.SMTP(host, port, timeout=20)
            s.ehlo()
            s.starttls(context=ctx)
            s.ehlo()
        with s:
            s.login(user, pw)
    except Exception as e:  # noqa: BLE001
        sys.exit("Login failed, nothing saved: %s" % e)

    for v in (host, user, pw, from_addr, from_name, reply_to):
        if "\n" in v or "\r" in v:
            sys.exit("line breaks are not allowed; nothing saved")
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# written by set_smtp.py; read by mail_send_api.py on every request\n")
        for k, v in (("SMTP_HOST", host), ("SMTP_PORT", str(port)), ("SMTP_USER", user), ("SMTP_PASS", pw),
                     ("FROM_ADDRESS", from_addr), ("FROM_NAME", from_name), ("REPLY_TO", reply_to)):
            f.write("%s=%s\n" % (k, v))
    os.replace(tmp, CONFIG)
    os.chmod(CONFIG, 0o600)
    print("Saved %s. Open the Email tab in Proxious and send a test to yourself." % CONFIG)


if __name__ == "__main__":
    main()

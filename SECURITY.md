# Security policy

fwupd-webui runs privileged and can write firmware, so please report security problems
privately.

## Reporting

Use [GitHub's private reporting form](https://github.com/yuzi-co/fwupd-webui/security/advisories/new).
Please don't open a public issue.

Include what you found, how to reproduce it and which version you tested. This is a
small project maintained by one person, so there is no guaranteed response time.

## Supported versions

Only the latest release gets fixes.

## What the app protects

- Every page and API route needs the login. Only `/login` and static files are open.
- Flashing is off by default, and each flash needs the device name typed.
- System firmware and Secure Boot variables are refused on every deployment target.
- A firmware file must match the SHA-256 in fwupd's signed metadata before it is installed.

## What it does not protect

- **Traffic is plain HTTP.** Someone who can sniff your network can capture the password.
  Use a reverse proxy with TLS if you don't trust the network.
- **Root on the host has full control.** The container is privileged, and the password is
  stored in plain text in the environment or in `/var/lib/fwupd/.webui-password`.
- **There is one shared password.** There are no separate accounts and no audit trail of
  who did what.

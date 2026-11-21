# Security policy

remoteG90ctl controls a radio transmitter. Two kinds of problems count as
security issues:

- **Transmit safety:** any way to make the radio transmit without a lease,
  longer than the limits, outside the allowed bands, without an
  authenticated session, or to keep transmitting after the client is gone.
- **Access:** any way to use the web interface or the server without logging
  in, to take over an account, or to reach the guard directly.

## Reporting

Please **do not open a public issue** for these. Use GitHub's private
vulnerability reporting ("Report a vulnerability" on the Security tab of this
repository). Include the version (`remoteg90ctl status`), your configuration
without secrets, and the steps to reproduce — on the simulator if possible.

You can expect an answer within a few days. This is a hobby project with one
maintainer; there is no bug bounty.

## Deployment assumptions

- The server listens only on loopback. Do not put it behind a public reverse
  proxy or port forwarding, and never use `tailscale funnel`; remote access is
  meant to go through a VPN (e.g. Tailscale with `tailscale serve`) and HTTPS.
  Under a tailnet name the server answers only requests with Tailscale's
  identity header (decisions.md D57), so Funnel requests are refused.
- Until the first account is created, anyone who can reach the page can
  create it. Create it right after installation.
- VOX in the radio bypasses every protection of this program. Keep it off.

## Supported versions

Only the latest release and `main` receive fixes.

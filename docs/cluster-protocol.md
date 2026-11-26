# DX cluster process protocol (version 1)

`remoteg90ctl-cluster` keeps one telnet connection to the DX cluster the
administrator set and passes the spots to the server (decisions.md D63,
`AGENTS.md` section 10). It is the only remoteG90ctl process with network
access beyond loopback; it has no path to the guard, the radio, the station
log or transmitting.

## Socket

A Unix stream socket, `/run/remoteg90ctl-cluster.sock` by default (the
server's `[cluster] socket_path`). Under systemd it belongs to
`remoteg90ctl-cluster.socket` (owner `remoteg90ctl`, mode 0600: only the
server's user may connect) and the process, started on the first connection,
runs as a user of its own (`DynamicUser`) and reads no configuration. Without
systemd it binds `--socket PATH` itself.

One client at a time: the server. A new connection replaces the old one.
JSON Lines, one object per line, each with `"v": 1`.

## Server → process

| `type` | Fields | |
|---|---|---|
| `configure` | `host` (a host name or IP address), `port` (1–65535), `call` (a callsign, optionally `-N`) | connect to this cluster and log in with `call`; replaces the cluster before |
| `configure` | `host`: `null` | no cluster: disconnect |

Nothing else can be passed. The server sends `configure` on every connection
and when the administrator changes the cluster.

## Process → server

| `event` | Fields | |
|---|---|---|
| `status` | `state` (`off`, `connecting`, `connected`, `retrying`), `message` | on connection and on every change |
| `spot` | `spotter`, `freq_khz`, `dx`, `comment` (≤ 64 printable characters), `utc` (`HHMM`, as the cluster gives it), `locator` (or `null`), `received_ms` (Unix time) | for each whole spot line |
| `error` | `message` | a request that was refused (bad JSON, version, type, host, port or callsign) |

## The cluster's side

The process sends the cluster the callsign once, after the login prompt
(`login:`, `call:`, `callsign:`) or 5 s after connecting, and nothing else: no
commands, spots or messages. Telnet negotiation from the cluster is read and
dropped. Lines are at most 512 characters (a longer one is dropped whole) and
only lines that parse whole as `DX de SPOTTER: FREQ DXCALL COMMENT HHMMZ
[LOCATOR]` become spots. A lost connection is tried again after 5, 10, 30, 60,
120 and then every 300 s; a cluster silent for 15 minutes is connected to
again.

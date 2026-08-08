# Device API Summary

All device routes are under `/api/v1/` and use JSON.

## Enrollment

`POST devices/enroll/`

Consumes a six-digit, single-use, 15-minute enrollment code plus Android and
integrity metadata. Returns a device-specific refresh credential and a one-hour
access token. The refresh credential must be stored with Android Keystore.

`POST devices/token/`

Exchanges a valid, non-revoked refresh credential for a short-lived access
token.

## Operations

`GET devices/sync/`

Returns server time and one of:

- `play`: immutable playlist manifest and expiring media URLs
- `fallback`: bundled DUDU media should play
- `maintenance`: advertising must stop and maintenance state must persist

`POST devices/heartbeat/`

Accepts the server-corrected recorded time, screen state, battery percentage,
storage, application and Android versions, optional temperature, active
playlist, sync state, and playback state. New APKs omit `external_power` and
`charging`: advertising and health policy do not depend on either value. The
server accepts and preserves those legacy fields during a phased APK rollout,
but does not use them to infer vehicle power, vehicle operation, occupancy, or
audience exposure.

`POST devices/operational-events/`

Accepts an idempotent event UUID, recorded time, kind, and non-sensitive
details. The battery-backed policy adds exactly these diagnostics:

- `planned_shutdown` with `details` equal to `{}` after a user confirms the
  visible **Prepare for shutdown** action.
- `abnormal_app_exit` with `details` containing exactly `reason`, whose value
  is one of `crash`, `native_crash`, `anr`, `initialization_failure`,
  `low_memory`, `excessive_resource_usage`, or `freezer_termination`.

Do not send stack traces, free-form process data, personal data, or power-state
claims in an operational event. The server uses received time and distinct event
IDs when evaluating repeated abnormal exits.

`POST devices/playback-batches/`

Accepts an idempotent loop batch containing one result per playlist entry.
Duplicate batch IDs are acknowledged without creating duplicate evidence.
The Android player sends the exact JSON document with
`Content-Type: application/json` and `Content-Encoding: gzip`; retries retain
the same batch and event IDs. Disabled devices cannot submit playback.

## Security

Use TLS only. Never log bearer or refresh tokens. Server authorization derives
the device from the access token and ignores any client-supplied device or
assignment identity.

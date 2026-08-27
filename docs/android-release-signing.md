# Android Production Signing and Sideloading

Generate the production keystore outside the repository and store two encrypted
copies under company control. Never send it through chat or commit it.

The release build requires these environment variables:

```text
DUDU_SIGNING_STORE_FILE
DUDU_SIGNING_STORE_PASSWORD
DUDU_SIGNING_KEY_ALIAS
DUDU_SIGNING_KEY_PASSWORD
```

It also requires the non-secret Google Cloud numeric project number. For the
current reviewed canary APK, use the retained production code `1` as the prior
value and build code `2` as version `1.0.1`:

```sh
./gradlew :app:assembleProductionRelease \
  -PproductionApiBaseUrl=https://api.marketing.duducaradmin.com/api/v1/ \
  -PplayIntegrityProjectNumber=123456789012 \
  -PpreviousProductionVersionCode=1 \
  -PproductionVersionCode=2 \
  -PproductionVersionName=1.0.1 \
  --no-daemon
```

Use the exact version code from the last deployed release as
`previousProductionVersionCode`; the new code must be strictly greater. The
build fails closed when signing, the explicit HTTPS API URL, non-zero Play
Integrity project number, semantic version name, or monotonic version-code
inputs are missing or invalid. There is no production debug variant and no
development release variant.

Before the canary, set Terraform `required_app_version` in the reviewed
production tfvars to this exact `productionVersionName`. The server compares
that value to the APK version name in every heartbeat; a mismatch produces an
outdated-app alert even when the APK is otherwise valid.

## DUDU-owned OTA updates

Build each OTA candidate with the same production keystore and a strictly higher
`productionVersionCode`. Upload the signed APK to the private media bucket under
an `updates/*.apk` key, record its lowercase SHA-256 and exact byte size, and set
the matching Terraform `app_update_*` values plus a rollout percentage. The
reviewed release-config and activation documents carry those values to the host;
the backend then advertises a short-lived signed CloudFront URL in `devices/sync/`.

The player accepts an update only in production while DUDU remains device owner,
the device is not in shutdown or administrator mode, storage has headroom, and
the tablet is charging or at least 50% battery. It verifies package name, version
code, APK digest, and the installed signing certificate before using Android's
silent device-owner PackageInstaller flow. A failed or interrupted install is
retried on a later sync and never replaces the current playback files.

Existing 1.0.1/code-2 tablets do not contain this updater. Install the first
updater-enabled release manually after Setup Wizard and device-owner assignment;
subsequent higher-code releases can be delivered OTA. Keep the previous same-key
APK for rollback, and disable OTA by setting all `app_update_*` values back to
their zero/empty defaults before selecting a release that should not advertise an
update.

## Current reviewed canary release identity — 2026-08-24

The current canary candidate uses previous version code `1`, production version
code `2`, and production version name `1.0.1`. The reviewed production tfvars
must explicitly set `required_app_version = "1.0.1"` with the final immutable
backend, PostgreSQL, and Caddy image digests. Do not infer a production release
from the Terraform default or from a tag.

Verify the APK certificate with Android
`apksigner verify --verbose --print-certs`, record its SHA-256 fingerprint and
the APK SHA-256 checksum, install it on one factory-reset canary, and retain the
previous same-key APK and R8 mapping file for manual rollback.

Before the canary, configure the production runtime
`PLAY_INTEGRITY_APP_CERTIFICATE_SHA256` with the comma-separated, 64-character
hex certificate fingerprint(s) from that command. This is an allowlist, not a
signing secret; do not put a real value in source control. Production readiness
fails closed when it is absent or malformed.

Enrollment is allowed only when Google returns `MEETS_DEVICE_INTEGRITY`, the
expected package, and a certificate digest matching that runtime allowlist. The
server accepts `PLAY_RECOGNIZED` and the staff-sideload
`UNRECOGNIZED_VERSION` verdict only with that matching certificate; it does not
ignore app recognition. A tablet that is not Play Protect certified is not
eligible for production enrollment.

The production application also refuses enrollment and playback unless its
package is the active device owner. Provision only a factory-reset qualified
tablet, then confirm ownership before opening the app:

```sh
adb shell dpm set-device-owner \
  com.duducar.signage/.KioskDeviceAdminReceiver
adb shell appops set com.duducar.signage SCHEDULE_EXACT_ALARM allow
adb shell appops get com.duducar.signage SCHEDULE_EXACT_ALARM
adb shell dpm list-owners
adb shell dumpsys device_policy
```

Locked kiosk policy blocks manual date/time configuration. Within one boot the
player advances its last server-time anchor using Android monotonic elapsed
time, so changing the wall clock cannot shift scheduling or proof timestamps.
After a reboot it uses the device-owner-protected wall clock only to account for
powered-off time until the next server synchronization.

Do this before the first player launch applies its kiosk restrictions. If the
qualified firmware does not support the shown `appops` command, grant DUDU
Signage the Android **Alarms & reminders** special access in system settings
before first launch. Verify `AlarmManager.canScheduleExactAlarms()` through the
administrator-session test. The player uses it only for the one-shot,
five-minute relock alarm and refuses to open Android settings when that durable
relock cannot be scheduled.

Keep USB debugging under staff control for manual sideloading and recovery.
Before enrollment, locked kiosk deliberately leaves USB transfer unrestricted so
a previously authorized staff ADB connection survives device-owner startup.
After enrollment, locked kiosk blocks USB transfer; a PIN-authenticated
administrator session temporarily restores it for staff work, and either manual
or timed relock reapplies the restriction. These paths do not enable USB
debugging or accept Android's RSA authorization prompt automatically. Disable
USB debugging for normal vehicle operation only after the exact model and
firmware pass the documented recovery and update rehearsal.

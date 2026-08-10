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
first-ever production APK, use prior version code `0` and a new version code of
`1` (replace the sample version name with the approved release value):

```sh
./gradlew :app:assembleProductionRelease \
  -PproductionApiBaseUrl=https://api.marketing.duducaradmin.com/api/v1/ \
  -PplayIntegrityProjectNumber=123456789012 \
  -PpreviousProductionVersionCode=0 \
  -PproductionVersionCode=1 \
  -PproductionVersionName=1.0.0 \
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

## Approved first production release identity — 2026-08-08

The pending first production canary uses previous version code `0`, production
version code `1`, and production version name `1.0.0`. The production tfvars
must keep `required_app_version = "1.0.0"` until a later, explicitly reviewed
APK release replaces it.

Verify the APK certificate with Android
`apksigner verify --verbose --print-certs`, record its SHA-256 fingerprint and
the APK SHA-256 checksum, install it on one factory-reset canary, and retain the
previous same-key APK and R8 mapping file for manual rollback.

Enrollment is allowed only when Google returns `MEETS_DEVICE_INTEGRITY`. The
pilot intentionally ignores Play licensing and app-recognition verdicts because
staff sideload the APK. A tablet that is not Play Protect certified is not
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
Disable it for normal vehicle operation only after the exact model and firmware
pass the documented recovery and update rehearsal.

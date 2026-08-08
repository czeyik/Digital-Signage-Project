# Android Display Qualification

Complete this checklist for the exact model and firmware before purchasing the
10 pilot units. Record model, firmware, test date, tester, and evidence.
Create a `HardwareQualification` record in the dashboard administration area
for each tested model/firmware. Do not mark `approved_for_pilot` until every
required and failure test has passed and the evidence reference points to the
supporting photos, logs, and notes.

## Required

- Android 12 or newer with current security updates
- 10-inch landscape display and built-in 4G LTE
- Device-owner provisioning and lock-task mode work after factory reset
- Navigation and status controls cannot escape the kiosk
- DUDU remains the persistent Home application after reboot and power reconnect
- Safe boot, secondary-user creation, app force-stop/control, overlay windows,
  manual date/time changes, physical-media mounting, and USB file transfer are
  blocked by the recorded device-owner policy
- Physical screen-on state is available and matches visual inspection
- Application starts automatically after vehicle power is connected
- Locked boot exposes no credential-protected player state; playback starts
  after the first unlock and ordinary `BOOT_COMPLETED`
- Battery-backed model reports power loss before playback stops, and the DUDU
  window releases its keep-screen-on request so Android/device policy can sleep
  or shut down the tablet
- Battery-free model restarts reliably and permits heartbeat-gap inference
- H.264 1080p playback is smooth for a continuous 12-hour test
- JPEG and PNG display correctly without cropping
- 10 GB cache and 500 MB evidence queue fit with at least 2 GB free
- SIM reconnects after tunnel, dead-zone, and network handover tests
- Device remains stable during interrupted and resumed downloads
- Thermal behavior stays within manufacturer limits in a representative car
- Battery protection and charging behavior are suitable for 10-12 hour shifts
- Approved mount, cable, fuse, and adapter do not create a road-safety hazard

## Failure Tests

- Disconnect power during every media type.
- Disconnect data during download, activation, playback, and event upload.
- Kill the DUDU process immediately before and after an item completes and at a
  loop boundary. Confirm the interrupted/orphaned loop is queued exactly once,
  every entry has a result, and playback resumes at the correct entry without
  counting powered-off time as played time.
- Corrupt a cached file and confirm the current playlist continues.
- Attempt kiosk escape through reboot, notifications, USB, accessibility,
  safe mode, and physical buttons.
- In maintenance or fallback, submit four wrong administrator PINs, then verify
  the fifth locks attempts for one minute, the sixth for five minutes, and
  subsequent failures for fifteen minutes. Reboot must not clear the throttle.
- Enter the correct administrator PIN, open Android settings, and verify the
  tablet automatically returns to locked DUDU playback within five minutes.
  Also verify the **Relock now** control. HOME ownership and screenshot blocking
  must remain active throughout the administrator session.
- Repeat the administrator-session test after killing the DUDU process while
  Android settings is foreground. The non-exported exact-alarm relock receiver
  must reapply device policy and return to DUDU at the original five-minute
  deadline. Remove **Alarms & reminders** access and confirm administrator mode
  refuses to open rather than relying only on an in-process timer.
- Verify staff can still sideload a same-key, higher-version APK and recover the
  tablet through the approved ADB/factory-reset procedure. The player does not
  disable factory reset or ADB globally because that would remove the pilot's
  unpaid recovery path; physical access remains a documented pilot risk.
- Attempt cloud backup and Android device-to-device transfer and confirm device
  tokens, administrator verifier/state, cached media, manifests, and proof queue
  are not restored to another tablet.
- Attempt to change date/time while the kiosk policy is active and confirm it is
  blocked. During a controlled qualification build, change wall time within one
  boot and confirm the monotonic server-time anchor keeps scheduling stable.
- Disable the device remotely and reboot it offline; maintenance mode must
  remain.
- Factory reset the device and confirm old credentials no longer grant access
  after server revocation.

## Operating Rule

Do not leave displays inside parked vehicles. Operations must define removal,
storage, inspection, damaged-battery isolation, and incident procedures before
the pilot begins.

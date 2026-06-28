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
- Physical screen-on state is available and matches visual inspection
- Application starts automatically after vehicle power is connected
- Battery-backed model reports power loss before playback stops
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
- Corrupt a cached file and confirm the current playlist continues.
- Attempt kiosk escape through reboot, notifications, USB, accessibility,
  safe mode, and physical buttons.
- Change device time and confirm server-corrected scheduling remains stable.
- Disable the device remotely and reboot it offline; maintenance mode must
  remain.
- Factory reset the device and confirm old credentials no longer grant access
  after server revocation.

## Operating Rule

Do not leave displays inside parked vehicles. Operations must define removal,
storage, inspection, damaged-battery isolation, and incident procedures before
the pilot begins.

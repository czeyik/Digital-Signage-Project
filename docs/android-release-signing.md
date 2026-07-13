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

It also requires the non-secret Google Cloud numeric project number:

```sh
./gradlew :app:assembleRelease \
  -PapiBaseUrl=https://api.marketing.duducaradmin.com/api/v1/ \
  -PplayIntegrityProjectNumber=123456789012 \
  --no-daemon
```

The build fails closed when a release task lacks signing configuration. Verify
the APK certificate with Android `apksigner verify --print-certs`, record its
SHA-256 fingerprint, install it on one factory-reset canary, and retain the
previous signed APK for manual rollback.

Enrollment is allowed only when Google returns `MEETS_DEVICE_INTEGRITY`. The
pilot intentionally ignores Play licensing and app-recognition verdicts because
staff sideload the APK. A tablet that is not Play Protect certified is not
eligible for production enrollment.

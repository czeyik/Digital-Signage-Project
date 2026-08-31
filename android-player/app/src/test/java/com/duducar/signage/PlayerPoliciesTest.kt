package com.duducar.signage

import android.app.ApplicationExitInfo
import java.io.ByteArrayInputStream
import java.time.Instant
import java.util.zip.GZIPInputStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

class PlayerPoliciesTest {
    @Test
    fun playlistTransitionUsesTheServerClockAndNeverSchedulesNegativeDelay() {
        val now = Instant.parse("2026-08-31T03:59:30Z")
        assertEquals(
            30_000L,
            PlaylistSyncPolicy.delayUntil(now, Instant.parse("2026-08-31T04:00:00Z")),
        )
        assertEquals(
            0L,
            PlaylistSyncPolicy.delayUntil(now, Instant.parse("2026-08-31T03:59:00Z")),
        )
    }

    @Test
    fun locationPolicyAcceptsOnlyFreshPreciseNonMockGpsOrNetworkFixes() {
        assertTrue(LocationPolicy.acceptsFix("gps", 100f, 0, false))
        assertTrue(LocationPolicy.acceptsFix("network", 50f, 120_000, false))
        assertFalse(LocationPolicy.acceptsFix("gps", 100.1f, 0, false))
        assertFalse(LocationPolicy.acceptsFix("passive", 10f, 0, false))
        assertFalse(LocationPolicy.acceptsFix("gps", 10f, 120_001, false))
        assertFalse(LocationPolicy.acceptsFix("gps", 10f, 0, true))
    }

    @Test
    fun locationPolicyDerivesFleetFreshnessStates() {
        assertEquals("initializing", LocationPolicy.stateFor(null))
        assertEquals("fresh", LocationPolicy.stateFor(179_999))
        assertEquals("stale", LocationPolicy.stateFor(180_000))
        assertEquals("unavailable", LocationPolicy.stateFor(600_000))
        assertEquals("initializing", LocationPolicy.stateForNoFix(179_999))
        assertEquals("stale", LocationPolicy.stateForNoFix(180_000))
        assertEquals("unavailable", LocationPolicy.stateForNoFix(600_000))
    }

    @Test
    fun appUpdatePolicyRejectsMalformedOrNonIncreasingArtifacts() {
        val currentVersion = BuildConfig.VERSION_CODE
        assertNotNull(
            AppUpdatePolicy.parse(
                currentVersion + 1,
                "1.0.2",
                "https://media.example.invalid/updates/player.apk",
                "a".repeat(64),
                1_000_000,
                currentVersion,
            ),
        )
        assertFalse(
            AppUpdatePolicy.parse(
                currentVersion,
                "1.0.2",
                "https://media.example.invalid/updates/player.apk",
                "a".repeat(64),
                1_000_000,
                currentVersion,
            ) != null,
        )
        assertFalse(
            AppUpdatePolicy.parse(
                currentVersion + 1,
                "1.0.2",
                "http://media.example.invalid/player.apk",
                "a".repeat(64),
                1_000_000,
                currentVersion,
            ) != null,
        )
        assertFalse(
            AppUpdatePolicy.parse(
                currentVersion + 1,
                "1.0.2",
                "https://media.example.invalid/player.apk",
                "not-a-digest",
                1_000_000,
                currentVersion,
            ) != null,
        )
    }

    @Test
    fun appUpdatePolicyRequiresOwnerAndSafePowerConditions() {
        assertFalse(
            AppUpdatePolicy.mayStage(
                isProduction = true,
                isDeviceOwner = false,
                shutdownPrepared = false,
                adminSessionActive = false,
                usableBytes = 1_000_000_000,
                updateSizeBytes = 1_000_000,
                batteryPercent = 100,
                charging = true,
            ),
        )
        assertFalse(
            AppUpdatePolicy.mayStage(
                isProduction = true,
                isDeviceOwner = true,
                shutdownPrepared = false,
                adminSessionActive = false,
                usableBytes = 1_000_000_000,
                updateSizeBytes = 1_000_000,
                batteryPercent = 49,
                charging = false,
            ),
        )
        assertTrue(
            AppUpdatePolicy.mayStage(
                isProduction = true,
                isDeviceOwner = true,
                shutdownPrepared = false,
                adminSessionActive = false,
                usableBytes = 1_000_000_000,
                updateSizeBytes = 1_000_000,
                batteryPercent = 49,
                charging = true,
            ),
        )
        assertTrue(
            AppUpdatePolicy.mayStage(
                isProduction = true,
                isDeviceOwner = true,
                shutdownPrepared = false,
                adminSessionActive = false,
                usableBytes = 1_000_000_000,
                updateSizeBytes = 1_000_000,
                batteryPercent = 50,
                charging = false,
            ),
        )
    }

    @Test
    fun cachePolicyRejectsOversizedOrUnsafeReplacement() {
        assertFalse(StoragePolicy.canStage(11_000, 0, 11_000, 20_000, 10_000, 2_000))
        assertFalse(StoragePolicy.canStage(8_000, 0, 8_000, 9_000, 10_000, 2_000))
        assertFalse(StoragePolicy.canStage(8_000, 6_000, 5_000, 20_000, 10_000, 2_000))
        assertTrue(StoragePolicy.canStage(8_000, 4_000, 4_000, 9_000, 10_000, 2_000))
    }

    @Test
    fun queueLossEnforcesTheCapEvenWhenFreeSpaceIsHealthy() {
        assertFalse(StoragePolicy.shouldForceQueueLoss(0, 100, 500, 200))
        assertFalse(StoragePolicy.shouldForceQueueLoss(400, 300, 500, 200))
        assertTrue(StoragePolicy.shouldForceQueueLoss(400, 100, 500, 200))
        assertTrue(StoragePolicy.shouldForceQueueLoss(501, 10_000, 500, 200))
    }

    @Test
    fun queueLossTargetHandlesLowStorageBelowTheQueueMargin() {
        assertEquals(
            100L,
            StoragePolicy.forcedQueueRemovalTargetBytes(400, 100, 500, 200),
        )
        assertEquals(
            225L,
            StoragePolicy.forcedQueueRemovalTargetBytes(600, 100, 500, 200),
        )
        assertEquals(
            50L,
            StoragePolicy.forcedQueueRemovalTargetBytes(50, 0, 500, 200),
        )
        assertEquals(
            126L,
            StoragePolicy.forcedQueueRemovalTargetBytes(501, 10_000, 500, 200),
        )
    }

    @Test
    fun imageDecodeBoundsRejectOversizedBitmapsBeforeAllocation() {
        assertTrue(ImageDecodePolicy.hasSafeBounds(1920, 1080))
        assertFalse(ImageDecodePolicy.hasSafeBounds(1921, 1080))
        assertFalse(ImageDecodePolicy.hasSafeBounds(1920, 1081))
        assertFalse(ImageDecodePolicy.hasSafeBounds(0, 1080))
    }

    @Test
    fun apiResponseReaderCapsUnknownLengthBodies() {
        assertEquals(
            "ok",
            ApiResponsePolicy.readBounded(ByteArrayInputStream("ok".toByteArray())),
        )
        assertTrue(
            runCatching {
                ApiResponsePolicy.readBounded(
                    ByteArrayInputStream(ByteArray(ApiResponsePolicy.MAX_BYTES + 1)),
                )
            }.isFailure,
        )
    }

    @Test
    fun pinVerifierAcceptsOnlyTheConfiguredPin() {
        val salt = ByteArray(16) { it.toByte() }
        val iterations = 10_000
        val expected = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
            .generateSecret(PBEKeySpec("123456".toCharArray(), salt, iterations, 256))
            .encoded
            .joinToString("") { "%02x".format(it) }
        val verifier = listOf(
            "pbkdf2_sha256",
            iterations.toString(),
            salt.toHex(),
            expected,
        ).joinToString("$")

        assertTrue(PinVerifier.verify("123456", verifier))
        assertFalse(PinVerifier.verify("654321", verifier))
        assertFalse(PinVerifier.verify("123456", "invalid"))
    }

    @Test
    fun productionEnrollmentRequiresDeviceOwner() {
        assertFalse(EnrollmentPolicy.mayEnroll(isProduction = true, isDeviceOwner = false))
        assertTrue(EnrollmentPolicy.mayEnroll(isProduction = true, isDeviceOwner = true))
        assertTrue(EnrollmentPolicy.mayEnroll(isProduction = false, isDeviceOwner = false))
        assertTrue(EnrollmentPolicy.requiresIntegrity(isProduction = true))
        assertFalse(EnrollmentPolicy.requiresIntegrity(isProduction = false))
    }

    @Test
    fun sameManifestRestartsWheneverPlaybackIsNoLongerActive() {
        assertTrue(PlaybackTransitionPolicy.sameManifest("weekly", 4, "weekly", 4))
        assertTrue(
            PlaybackTransitionPolicy.shouldStart(
                mode = "play",
                hasActiveManifest = true,
                playbackActive = false,
                adminSessionActive = false,
            ),
        )
        assertFalse(
            PlaybackTransitionPolicy.shouldStart(
                mode = "play",
                hasActiveManifest = true,
                playbackActive = true,
                adminSessionActive = false,
            ),
        )
    }

    @Test
    fun playbackStartsWhenTheManifestIsReadyWithoutAnExternalPowerCondition() {
        assertTrue(
            PlaybackTransitionPolicy.shouldStart(
                mode = "play",
                hasActiveManifest = true,
                playbackActive = false,
                adminSessionActive = false,
            ),
        )
        assertFalse(
            PlaybackTransitionPolicy.shouldStart(
                mode = "maintenance",
                hasActiveManifest = true,
                playbackActive = false,
                adminSessionActive = false,
            ),
        )
        assertFalse(
            PlaybackTransitionPolicy.shouldStart(
                mode = "play",
                hasActiveManifest = true,
                playbackActive = false,
                adminSessionActive = true,
            ),
        )
    }

    @Test
    fun everyChangedManifestActivatesImmediatelyAtItsServerSelectedBoundary() {
        assertTrue(
            PlaybackTransitionPolicy.shouldActivateImmediately(
                hasActiveManifest = false,
                sameManifest = false,
            ),
        )
        assertTrue(
            PlaybackTransitionPolicy.shouldActivateImmediately(
                hasActiveManifest = true,
                sameManifest = false,
            ),
        )
        assertFalse(
            PlaybackTransitionPolicy.shouldActivateImmediately(
                hasActiveManifest = true,
                sameManifest = true,
            ),
        )
    }

    @Test
    fun pinFailuresEscalateAndAdminSessionIsStrictlyBounded() {
        val now = 1_000_000L
        var state = PinThrottleState(0, 0)
        repeat(4) { state = KioskAdminPolicy.afterFailure(state, now) }
        assertEquals(0, KioskAdminPolicy.remainingLockoutMs(state, now))

        state = KioskAdminPolicy.afterFailure(state, now)
        assertEquals(60_000, KioskAdminPolicy.remainingLockoutMs(state, now))
        state = KioskAdminPolicy.afterFailure(state, now + 60_000)
        assertEquals(300_000, KioskAdminPolicy.remainingLockoutMs(state, now + 60_000))

        assertEquals(
            KioskAdminPolicy.SESSION_DURATION_MS,
            KioskAdminPolicy.remainingSessionMs(
                Long.MAX_VALUE,
                Long.MAX_VALUE,
                now,
                now,
            ),
        )
        assertEquals(
            30_000,
            KioskAdminPolicy.remainingSessionMs(
                now + 60_000,
                now + 30_000,
                now,
                now,
            ),
        )
        assertEquals(
            0,
            KioskAdminPolicy.remainingSessionMs(
                now - 1,
                now + 60_000,
                now,
                now,
            ),
        )
    }

    @Test
    fun usbTransferIsLockedOnlyForEnrolledProductionDevices() {
        assertFalse(
            KioskAdminPolicy.shouldRestrictUsbFileTransfer(
                isEnrolled = false,
                isProduction = true,
            ),
        )
        assertTrue(
            KioskAdminPolicy.shouldRestrictUsbFileTransfer(
                isEnrolled = true,
                isProduction = true,
            ),
        )
        assertFalse(
            KioskAdminPolicy.shouldRestrictUsbFileTransfer(
                isEnrolled = true,
                isProduction = false,
            ),
        )
    }

    @Test
    fun onlyVisibleActiveMediaKeepsThePlaybackWindowAwake() {
        assertTrue(
            ScreenAwakePolicy.shouldKeepScreenAwake(
                playbackActive = true,
                visibleMedia = true,
            ),
        )
        assertFalse(
            ScreenAwakePolicy.shouldKeepScreenAwake(
                playbackActive = true,
                visibleMedia = false,
            ),
        )
        assertFalse(
            ScreenAwakePolicy.shouldKeepScreenAwake(
                playbackActive = false,
                visibleMedia = true,
            ),
        )
    }

    @Test
    fun correctedClockIgnoresWallClockChangesDuringTheSameBoot() {
        val anchor = ServerClockAnchor(
            serverEpochMs = 10_000,
            wallEpochMs = 5_000,
            elapsedRealtimeMs = 100,
            bootCount = 7,
        )

        assertEquals(
            10_060,
            CorrectedClockPolicy.nowEpochMs(
                anchor,
                currentWallEpochMs = 5_000_000,
                currentElapsedRealtimeMs = 160,
                currentBootCount = 7,
            ),
        )
    }

    @Test
    fun correctedClockUsesProtectedWallTimeOnlyAfterReboot() {
        val anchor = ServerClockAnchor(
            serverEpochMs = 10_000,
            wallEpochMs = 5_000,
            elapsedRealtimeMs = 2_000,
            bootCount = 7,
        )

        assertEquals(
            11_500,
            CorrectedClockPolicy.nowEpochMs(
                anchor,
                currentWallEpochMs = 6_500,
                currentElapsedRealtimeMs = 50,
                currentBootCount = 8,
            ),
        )
        assertEquals(
            10_000,
            CorrectedClockPolicy.nowEpochMs(
                anchor,
                currentWallEpochMs = 4_000,
                currentElapsedRealtimeMs = 50,
                currentBootCount = 8,
            ),
        )
    }

    @Test
    fun playbackRecoveryResumesTheCorrectEntryWithoutCountingDowntime() {
        val entries = listOf("first", "second", "third")
        assertEquals(
            1,
            PlaybackRecoveryPolicy.resumeIndex(entries, listOf("first"), checkpointIndex = 1),
        )
        assertEquals(
            1,
            PlaybackRecoveryPolicy.resumeIndex(
                entries,
                listOf("first", "second"),
                checkpointIndex = 1,
            ),
        )
        assertEquals(
            2,
            PlaybackRecoveryPolicy.resumeIndex(
                entries,
                listOf("first", "second"),
                checkpointIndex = null,
            ),
        )
        assertEquals(
            250,
            PlaybackRecoveryPolicy.recoveredInterruptionDurationMs(1_000, 5, 1_250, 5),
        )
        assertEquals(
            0,
            PlaybackRecoveryPolicy.recoveredInterruptionDurationMs(1_000, 5, 20, 6),
        )
    }

    @Test
    fun playbackBatchesUseGzipJsonWithoutChangingThePayload() {
        val json = """{"id":"same-on-every-retry","events":[{"status":"completed"}]}"""
        val encoded = PlaybackBatchTransport.encodeJson(json)

        assertEquals("application/json", PlaybackBatchTransport.CONTENT_TYPE)
        assertEquals("gzip", PlaybackBatchTransport.CONTENT_ENCODING)
        assertEquals(0x1f, encoded[0].toInt() and 0xff)
        assertEquals(0x8b, encoded[1].toInt() and 0xff)
        val decoded = GZIPInputStream(ByteArrayInputStream(encoded)).bufferedReader().use {
            it.readText()
        }
        assertEquals(json, decoded)
    }

    @Test
    fun plannedShutdownSuppressesOnlyTheMatchingOrderlyExitAndPreservesRecoveryReason() {
        val marker = PlannedShutdownMarker(
            id = "a7a8934a-3f69-4d30-83da-55f963a24dcd",
            preparedAtEpochMs = 1_000,
            orderlyShutdownAtEpochMs = 2_000,
        )

        assertTrue(
            ShutdownPreparationPolicy.shouldSuppressAbnormalExit(
                marker = marker,
                exitTimestampMs = 2_000,
                nowEpochMs = 2_001,
            ),
        )
        assertTrue(
            ShutdownPreparationPolicy.shouldSuppressAbnormalExit(
                marker = marker,
                exitTimestampMs = 2_000 + ShutdownPreparationPolicy.ORDERLY_EXIT_MATCH_WINDOW_MS,
                nowEpochMs = 2_000 + ShutdownPreparationPolicy.ABNORMAL_EXIT_SUPPRESSION_WINDOW_MS,
            ),
        )
        assertFalse(
            ShutdownPreparationPolicy.shouldSuppressAbnormalExit(
                marker = marker,
                exitTimestampMs = 1_999,
                nowEpochMs = 2_001,
            ),
        )
        assertFalse(
            ShutdownPreparationPolicy.shouldSuppressAbnormalExit(
                marker = marker,
                exitTimestampMs = 2_000 + ShutdownPreparationPolicy.ORDERLY_EXIT_MATCH_WINDOW_MS + 1,
                nowEpochMs = 2_001,
            ),
        )
        val resumedMarker = marker.copy(resumedAtEpochMs = 2_010)
        assertTrue(
            ShutdownPreparationPolicy.shouldSuppressAbnormalExit(
                marker = resumedMarker,
                exitTimestampMs = 2_010,
                nowEpochMs = 2_011,
            ),
        )
        assertFalse(
            ShutdownPreparationPolicy.shouldSuppressAbnormalExit(
                marker = resumedMarker,
                exitTimestampMs = 2_011,
                nowEpochMs = 2_011,
            ),
        )
        assertFalse(
            ShutdownPreparationPolicy.shouldSuppressAbnormalExit(
                marker = marker,
                exitTimestampMs = 2_000,
                nowEpochMs = 2_000 + ShutdownPreparationPolicy.ABNORMAL_EXIT_SUPPRESSION_WINDOW_MS + 1,
            ),
        )
        assertEquals(
            "planned_shutdown",
            ShutdownPreparationPolicy.recoveredInterruptionReason(marker),
        )
        assertEquals(
            "app_restart_or_unexpected_exit",
            ShutdownPreparationPolicy.recoveredInterruptionReason(null),
        )
        assertFalse(ShutdownPreparationPolicy.shouldResumeAutomatically(true))
        assertTrue(ShutdownPreparationPolicy.shouldResumeAutomatically(false))
        assertTrue(ShutdownPreparationPolicy.requiresTrustedTimestampRebase(false))
        assertFalse(ShutdownPreparationPolicy.requiresTrustedTimestampRebase(true))
    }

    @Test
    fun exitHistoryIsPerInstallDeterministicAndAdvancesWithoutReplayingTheCursor() {
        val installationA = "f0e647d0-e625-4c68-ad1a-a9aed0c7f90e"
        val installationB = "f6de5efd-4c45-4d68-8550-9f6455a02ec7"
        val crash = ExitHistoryEntry(1_000, ApplicationExitInfo.REASON_CRASH)
        val anr = ExitHistoryEntry(2_000, ApplicationExitInfo.REASON_ANR)
        val crashId = ExitHistoryPolicy.stableEventId(installationA, crash)

        assertFalse(ExitHistoryPolicy.shouldCollectDiagnostics(false))
        assertTrue(ExitHistoryPolicy.shouldCollectDiagnostics(true))
        assertEquals(crashId, ExitHistoryPolicy.stableEventId(installationA, crash))
        assertFalse(crashId == ExitHistoryPolicy.stableEventId(installationB, crash))
        assertEquals("crash", ExitHistoryPolicy.abnormalReason(crash.androidReason, false))
        assertEquals("anr", ExitHistoryPolicy.abnormalReason(anr.androidReason, false))
        assertEquals(
            "freezer_termination",
            ExitHistoryPolicy.abnormalReason(
                ApplicationExitInfo.REASON_FREEZER,
                supportsFreezerTermination = true,
            ),
        )
        assertEquals(
            null,
            ExitHistoryPolicy.abnormalReason(
                ApplicationExitInfo.REASON_FREEZER,
                supportsFreezerTermination = false,
            ),
        )

        val cursor = ExitHistoryCursor(1_000, setOf(crashId))
        assertEquals(
            listOf(anr),
            ExitHistoryPolicy.unprocessedEntries(installationA, listOf(crash, anr), cursor),
        )
        assertEquals(
            ExitHistoryCursor(
                timestampMs = 2_000,
                identitiesAtTimestamp = setOf(ExitHistoryPolicy.stableEventId(installationA, anr)),
            ),
            ExitHistoryPolicy.advanceCursor(installationA, cursor, listOf(crash, anr)),
        )
    }

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
}

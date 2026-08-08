package com.duducar.signage

import java.io.ByteArrayInputStream
import java.util.zip.GZIPInputStream
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

class PlayerPoliciesTest {
    @Test
    fun cachePolicyRejectsOversizedOrUnsafeReplacement() {
        assertFalse(StoragePolicy.canStage(11_000, 0, 11_000, 20_000, 10_000, 2_000))
        assertFalse(StoragePolicy.canStage(8_000, 0, 8_000, 9_000, 10_000, 2_000))
        assertFalse(StoragePolicy.canStage(8_000, 6_000, 5_000, 20_000, 10_000, 2_000))
        assertTrue(StoragePolicy.canStage(8_000, 4_000, 4_000, 9_000, 10_000, 2_000))
    }

    @Test
    fun queueLossRequiresQueuedEvidenceAndCriticalFreeSpace() {
        assertFalse(StoragePolicy.shouldForceQueueLoss(0, 100, 200))
        assertFalse(StoragePolicy.shouldForceQueueLoss(600, 300, 200))
        assertTrue(StoragePolicy.shouldForceQueueLoss(400, 100, 200))
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
    }

    @Test
    fun sameManifestRestartsWheneverPlaybackIsNoLongerActive() {
        assertTrue(PlaybackTransitionPolicy.sameManifest("weekly", 4, "weekly", 4))
        assertTrue(
            PlaybackTransitionPolicy.shouldStart(
                hasExternalPower = true,
                mode = "play",
                hasActiveManifest = true,
                playbackActive = false,
                adminSessionActive = false,
            ),
        )
        assertFalse(
            PlaybackTransitionPolicy.shouldStart(
                hasExternalPower = true,
                mode = "play",
                hasActiveManifest = true,
                playbackActive = true,
                adminSessionActive = false,
            ),
        )
    }

    @Test
    fun reconnectAndReactivationStayFailClosedUntilEveryConditionIsReady() {
        assertFalse(
            PlaybackTransitionPolicy.shouldStart(
                hasExternalPower = false,
                mode = "play",
                hasActiveManifest = true,
                playbackActive = false,
                adminSessionActive = false,
            ),
        )
        assertFalse(
            PlaybackTransitionPolicy.shouldStart(
                hasExternalPower = true,
                mode = "maintenance",
                hasActiveManifest = true,
                playbackActive = false,
                adminSessionActive = false,
            ),
        )
        assertFalse(
            PlaybackTransitionPolicy.shouldStart(
                hasExternalPower = true,
                mode = "play",
                hasActiveManifest = true,
                playbackActive = false,
                adminSessionActive = true,
            ),
        )
    }

    @Test
    fun onlyFirstOrChangedUrgentManifestActivatesImmediately() {
        assertTrue(
            PlaybackTransitionPolicy.shouldActivateImmediately(
                hasActiveManifest = false,
                sameManifest = false,
                urgent = false,
            ),
        )
        assertTrue(
            PlaybackTransitionPolicy.shouldActivateImmediately(
                hasActiveManifest = true,
                sameManifest = false,
                urgent = true,
            ),
        )
        assertFalse(
            PlaybackTransitionPolicy.shouldActivateImmediately(
                hasActiveManifest = true,
                sameManifest = true,
                urgent = true,
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
    fun externalPowerAloneKeepsThePlaybackWindowAwake() {
        assertTrue(ExternalPowerPolicy.shouldKeepScreenAwake(hasExternalPower = true))
        assertFalse(ExternalPowerPolicy.shouldKeepScreenAwake(hasExternalPower = false))
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
    fun playbackRecoveryResumesTheCorrectEntryWithoutCountingPoweredOffTime() {
        val entries = listOf("first", "second", "third")
        assertEquals(
            1,
            PlaybackRecoveryPolicy.resumeIndex(entries, listOf("first"), checkpointIndex = 1),
        )
        assertEquals(
            2,
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

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
}

package com.duducar.signage

import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.util.zip.GZIPOutputStream
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

object StoragePolicy {
    fun canStage(
        requiredBytes: Long,
        cachedBytes: Long,
        downloadBytes: Long,
        usableBytes: Long,
        cacheLimitBytes: Long,
        minimumFreeBytes: Long,
    ): Boolean {
        if (requiredBytes < 0 || requiredBytes > cacheLimitBytes) return false
        if (downloadBytes < 0 || cachedBytes + downloadBytes > cacheLimitBytes) {
            return false
        }
        return usableBytes - downloadBytes >= minimumFreeBytes
    }

    fun shouldForceQueueLoss(
        queueBytes: Long,
        usableBytes: Long,
        minimumFreeBytes: Long,
    ): Boolean = queueBytes > 0 && usableBytes < minimumFreeBytes

    fun forcedQueueRemovalTargetBytes(
        queueBytes: Long,
        usableBytes: Long,
        maxQueueBytes: Long,
        minimumFreeBytes: Long,
    ): Long {
        if (!shouldForceQueueLoss(queueBytes, usableBytes, minimumFreeBytes)) return 0
        val bytesNeededForMinimumFree = (minimumFreeBytes - usableBytes).coerceAtLeast(0)
        val queueTarget = maxQueueBytes * 3 / 4
        val bytesNeededForQueueMargin = (queueBytes - queueTarget).coerceAtLeast(0)
        return maxOf(bytesNeededForMinimumFree, bytesNeededForQueueMargin)
            .coerceAtMost(queueBytes)
    }
}

object PinVerifier {
    fun verify(pin: String, verifier: String): Boolean {
        val parts = verifier.split("$")
        if (parts.size != 4 || parts[0] != "pbkdf2_sha256") return false
        return try {
            val iterations = parts[1].toInt()
            val salt = parts[2].chunked(2).map { it.toInt(16).toByte() }.toByteArray()
            val expected = parts[3]
            val spec = PBEKeySpec(pin.toCharArray(), salt, iterations, expected.length * 4)
            val actual = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
                .generateSecret(spec)
                .encoded
                .joinToString("") { "%02x".format(it) }
            MessageDigest.isEqual(actual.toByteArray(), expected.toByteArray())
        } catch (_: Exception) {
            false
        }
    }
}

data class PinThrottleState(
    val failedAttempts: Int,
    val lockedUntilEpochMs: Long,
)

object KioskAdminPolicy {
    const val SESSION_DURATION_MS = 5 * 60 * 1000L

    fun remainingLockoutMs(state: PinThrottleState, nowEpochMs: Long): Long =
        (state.lockedUntilEpochMs - nowEpochMs).coerceAtLeast(0)

    fun afterFailure(state: PinThrottleState, nowEpochMs: Long): PinThrottleState {
        val failures = (state.failedAttempts + 1).coerceAtMost(100)
        val delay = when {
            failures < 5 -> 0L
            failures == 5 -> 60 * 1000L
            failures == 6 -> 5 * 60 * 1000L
            else -> 15 * 60 * 1000L
        }
        return PinThrottleState(
            failedAttempts = failures,
            lockedUntilEpochMs = maxOf(state.lockedUntilEpochMs, nowEpochMs + delay),
        )
    }

    fun remainingSessionMs(
        sessionUntilEpochMs: Long,
        sessionUntilElapsedMs: Long,
        nowEpochMs: Long,
        nowElapsedMs: Long,
    ): Long = minOf(
        (sessionUntilEpochMs - nowEpochMs).coerceAtLeast(0),
        (sessionUntilElapsedMs - nowElapsedMs).coerceAtLeast(0),
        SESSION_DURATION_MS,
    )
}

object EnrollmentPolicy {
    fun mayEnroll(isProduction: Boolean, isDeviceOwner: Boolean): Boolean =
        !isProduction || isDeviceOwner
}

object ExternalPowerPolicy {
    fun shouldKeepScreenAwake(hasExternalPower: Boolean): Boolean = hasExternalPower
}

data class ServerClockAnchor(
    val serverEpochMs: Long,
    val wallEpochMs: Long,
    val elapsedRealtimeMs: Long,
    val bootCount: Int,
)

object CorrectedClockPolicy {
    fun nowEpochMs(
        anchor: ServerClockAnchor,
        currentWallEpochMs: Long,
        currentElapsedRealtimeMs: Long,
        currentBootCount: Int,
    ): Long {
        val sameBoot =
            anchor.bootCount >= 0 &&
                currentBootCount == anchor.bootCount &&
                currentElapsedRealtimeMs >= anchor.elapsedRealtimeMs
        val elapsed = if (sameBoot) {
            currentElapsedRealtimeMs - anchor.elapsedRealtimeMs
        } else {
            // Device-owner policy blocks manual date/time changes. After a
            // reboot elapsedRealtime resets, so the protected wall clock is
            // the only available way to account for powered-off time.
            (currentWallEpochMs - anchor.wallEpochMs).coerceAtLeast(0)
        }
        return if (Long.MAX_VALUE - anchor.serverEpochMs < elapsed) {
            Long.MAX_VALUE
        } else {
            anchor.serverEpochMs + elapsed
        }
    }
}

object PlaybackRecoveryPolicy {
    fun resumeIndex(
        manifestEntryIds: List<String>,
        recordedEntryIds: List<String>,
        checkpointIndex: Int?,
    ): Int {
        require(manifestEntryIds.isNotEmpty())
        if (checkpointIndex != null) {
            require(checkpointIndex in manifestEntryIds.indices)
            return if (recordedEntryIds.contains(manifestEntryIds[checkpointIndex])) {
                (checkpointIndex + 1) % manifestEntryIds.size
            } else {
                checkpointIndex
            }
        }
        require(recordedEntryIds.isNotEmpty())
        val lastIndex = manifestEntryIds.indexOf(recordedEntryIds.last())
        require(lastIndex >= 0)
        return (lastIndex + 1) % manifestEntryIds.size
    }

    fun recoveredInterruptionDurationMs(
        startedElapsedRealtimeMs: Long,
        startedBootCount: Int,
        currentElapsedRealtimeMs: Long,
        currentBootCount: Int,
    ): Long = if (
        startedElapsedRealtimeMs >= 0 &&
        startedBootCount >= 0 &&
        startedBootCount == currentBootCount &&
        currentElapsedRealtimeMs >= startedElapsedRealtimeMs
    ) {
        currentElapsedRealtimeMs - startedElapsedRealtimeMs
    } else {
        // The exact interruption point is unknowable after a power loss or
        // reboot. Reporting zero is safer than counting powered-off time.
        0
    }
}

object PlaybackBatchTransport {
    const val CONTENT_TYPE = "application/json"
    const val CONTENT_ENCODING = "gzip"

    fun encodeJson(json: String): ByteArray {
        val output = ByteArrayOutputStream()
        GZIPOutputStream(output).use { gzip ->
            gzip.write(json.toByteArray(Charsets.UTF_8))
        }
        return output.toByteArray()
    }
}

object PlaybackTransitionPolicy {
    fun sameManifest(
        activeId: String?,
        activeVersion: Int?,
        incomingId: String,
        incomingVersion: Int,
    ): Boolean = activeId == incomingId && activeVersion == incomingVersion

    fun shouldActivateImmediately(
        hasActiveManifest: Boolean,
        sameManifest: Boolean,
        urgent: Boolean,
    ): Boolean = !hasActiveManifest || (urgent && !sameManifest)

    fun shouldStart(
        hasExternalPower: Boolean,
        mode: String?,
        hasActiveManifest: Boolean,
        playbackActive: Boolean,
        adminSessionActive: Boolean,
    ): Boolean =
        hasExternalPower &&
            mode == "play" &&
            hasActiveManifest &&
            !playbackActive &&
            !adminSessionActive
}

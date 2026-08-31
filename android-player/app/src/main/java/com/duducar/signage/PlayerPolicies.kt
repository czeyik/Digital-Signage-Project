package com.duducar.signage

import android.app.ApplicationExitInfo
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.time.Duration
import java.time.Instant
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
        maxQueueBytes: Long,
        minimumFreeBytes: Long,
    ): Boolean = queueBytes > 0 && (
        queueBytes > maxQueueBytes || usableBytes < minimumFreeBytes
    )

    fun forcedQueueRemovalTargetBytes(
        queueBytes: Long,
        usableBytes: Long,
        maxQueueBytes: Long,
        minimumFreeBytes: Long,
    ): Long {
        if (!shouldForceQueueLoss(queueBytes, usableBytes, maxQueueBytes, minimumFreeBytes)) {
            return 0
        }
        val bytesNeededForMinimumFree = (minimumFreeBytes - usableBytes).coerceAtLeast(0)
        val queueTarget = maxQueueBytes * 3 / 4
        val bytesNeededForQueueMargin = (queueBytes - queueTarget).coerceAtLeast(0)
        return maxOf(bytesNeededForMinimumFree, bytesNeededForQueueMargin)
            .coerceAtMost(queueBytes)
    }
}

/** Published images are normalized to 1920×1080; never allocate beyond it. */
object ImageDecodePolicy {
    const val MAX_WIDTH = 1920
    const val MAX_HEIGHT = 1080
    const val MAX_PIXELS = MAX_WIDTH * MAX_HEIGHT

    fun hasSafeBounds(width: Int, height: Int): Boolean =
        width in 1..MAX_WIDTH &&
            height in 1..MAX_HEIGHT &&
            width.toLong() * height <= MAX_PIXELS.toLong()
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

    fun shouldRestrictUsbFileTransfer(isEnrolled: Boolean, isProduction: Boolean): Boolean =
        isEnrolled && isProduction

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

    fun requiresIntegrity(isProduction: Boolean): Boolean = isProduction
}

object ScreenAwakePolicy {
    fun shouldKeepScreenAwake(
        playbackActive: Boolean,
        visibleMedia: Boolean,
    ): Boolean = playbackActive && visibleMedia
}

data class PlannedShutdownMarker(
    val id: String,
    val preparedAtEpochMs: Long,
    val orderlyShutdownAtEpochMs: Long? = null,
    // Present only on the non-blocking marker retained after an explicit
    // Resume DUDU confirmation. It prevents a later, unrelated crash from
    // being classified as the preceding orderly shutdown.
    val resumedAtEpochMs: Long? = null,
    // When a shutdown is prepared before the first server-time anchor, its
    // queued event must be rebased before upload so a bad device clock cannot
    // make that FIFO record look implausibly far in the future to the API.
    val requiresTrustedTimestampRebase: Boolean = false,
)

object ShutdownPreparationPolicy {
    const val ABNORMAL_EXIT_SUPPRESSION_WINDOW_MS = 24L * 60 * 60 * 1000
    const val ORDERLY_EXIT_MATCH_WINDOW_MS = 5L * 60 * 1000

    fun shouldSuppressAbnormalExit(
        marker: PlannedShutdownMarker,
        exitTimestampMs: Long,
        nowEpochMs: Long,
    ): Boolean {
        val orderlyShutdownAt = marker.orderlyShutdownAtEpochMs ?: return false
        val markerAge = nowEpochMs - orderlyShutdownAt
        val lastMatchingExitAt = minOf(
            orderlyShutdownAt + ORDERLY_EXIT_MATCH_WINDOW_MS,
            marker.resumedAtEpochMs ?: Long.MAX_VALUE,
        )
        return exitTimestampMs >= orderlyShutdownAt &&
            exitTimestampMs <= lastMatchingExitAt &&
            marker.preparedAtEpochMs <= orderlyShutdownAt &&
            markerAge in 0..ABNORMAL_EXIT_SUPPRESSION_WINDOW_MS
    }

    fun recoveredInterruptionReason(marker: PlannedShutdownMarker?): String =
        if (marker != null) "planned_shutdown" else "app_restart_or_unexpected_exit"

    fun shouldResumeAutomatically(hasShutdownMarker: Boolean): Boolean = !hasShutdownMarker

    fun requiresTrustedTimestampRebase(hasTrustedServerAnchor: Boolean): Boolean =
        !hasTrustedServerAnchor
}

data class ExitHistoryEntry(
    val timestampMs: Long,
    val androidReason: Int,
)

data class ExitHistoryCursor(
    val timestampMs: Long = -1,
    val identitiesAtTimestamp: Set<String> = emptySet(),
)

object ExitHistoryPolicy {
    fun shouldCollectDiagnostics(hasTrustedServerAnchor: Boolean): Boolean =
        hasTrustedServerAnchor

    fun abnormalReason(
        androidReason: Int,
        supportsFreezerTermination: Boolean,
    ): String? = when (androidReason) {
        ApplicationExitInfo.REASON_CRASH -> "crash"
        ApplicationExitInfo.REASON_CRASH_NATIVE -> "native_crash"
        ApplicationExitInfo.REASON_ANR -> "anr"
        ApplicationExitInfo.REASON_INITIALIZATION_FAILURE -> "initialization_failure"
        ApplicationExitInfo.REASON_LOW_MEMORY -> "low_memory"
        ApplicationExitInfo.REASON_EXCESSIVE_RESOURCE_USAGE -> "excessive_resource_usage"
        ApplicationExitInfo.REASON_FREEZER -> if (supportsFreezerTermination) {
            "freezer_termination"
        } else {
            null
        }
        else -> null
    }

    fun stableEventId(
        installationIdentity: String,
        entry: ExitHistoryEntry,
    ): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(
            "duducar-abnormal-exit-v1|$installationIdentity|${entry.timestampMs}|${entry.androidReason}"
                .toByteArray(Charsets.UTF_8),
        )
        val uuidBytes = digest.copyOfRange(0, 16)
        uuidBytes[6] = ((uuidBytes[6].toInt() and 0x0f) or 0x50).toByte()
        uuidBytes[8] = ((uuidBytes[8].toInt() and 0x3f) or 0x80).toByte()
        val buffer = ByteBuffer.wrap(uuidBytes)
        return java.util.UUID(buffer.long, buffer.long).toString()
    }

    fun unprocessedEntries(
        installationIdentity: String,
        entries: Collection<ExitHistoryEntry>,
        cursor: ExitHistoryCursor,
    ): List<ExitHistoryEntry> = entries
        .asSequence()
        .filter { it.timestampMs >= 0 }
        .filter {
            it.timestampMs > cursor.timestampMs ||
                (
                    it.timestampMs == cursor.timestampMs &&
                        stableEventId(installationIdentity, it) !in cursor.identitiesAtTimestamp
                    )
        }
        .sortedWith(
            compareBy<ExitHistoryEntry> { it.timestampMs }
                .thenBy { stableEventId(installationIdentity, it) },
        )
        .toList()

    fun advanceCursor(
        installationIdentity: String,
        cursor: ExitHistoryCursor,
        entries: Collection<ExitHistoryEntry>,
    ): ExitHistoryCursor {
        val validEntries = entries.filter { it.timestampMs >= 0 }
        val latestTimestamp = validEntries.maxOfOrNull { it.timestampMs } ?: return cursor
        val latestIdentities = validEntries
            .asSequence()
            .filter { it.timestampMs == latestTimestamp }
            .map { stableEventId(installationIdentity, it) }
            .toSet()
        return when {
            latestTimestamp > cursor.timestampMs -> ExitHistoryCursor(
                timestampMs = latestTimestamp,
                identitiesAtTimestamp = latestIdentities,
            )
            latestTimestamp == cursor.timestampMs -> cursor.copy(
                identitiesAtTimestamp = cursor.identitiesAtTimestamp + latestIdentities,
            )
            else -> cursor
        }
    }
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
            // the only available way to account for the downtime.
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
            // The checkpoint identifies media interrupted by a process exit.
            // Its interrupted record belongs to the recovered prior loop; the
            // next loop must restart that same media from its beginning.
            return checkpointIndex
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
        // The exact interruption point is unknowable after a device reboot.
        // Reporting zero is safer than counting downtime as playback.
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
    ): Boolean = !hasActiveManifest || !sameManifest

    fun shouldStart(
        mode: String?,
        hasActiveManifest: Boolean,
        playbackActive: Boolean,
        adminSessionActive: Boolean,
    ): Boolean =
        mode == "play" &&
            hasActiveManifest &&
            !playbackActive &&
            !adminSessionActive
}

object PlaylistSyncPolicy {
    fun delayUntil(now: Instant, transition: Instant): Long =
        Duration.between(now, transition).toMillis().coerceAtLeast(0)
}

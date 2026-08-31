package com.duducar.signage

import android.Manifest
import android.content.ContentValues
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ApplicationInfo
import android.os.UserManager
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID

@RunWith(AndroidJUnit4::class)
class PlayerSecurityInstrumentationTest {
    @Test
    fun testManagementCredentialSurvivesPlaybackCredentialClear() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val credentials = CredentialStore(context)
        credentials.clearEnrollment()
        credentials.clearManagementToken()

        assertTrue(
            credentials.saveEnrollmentCredentials(
                refreshToken = "playback-secret",
                kioskPinVerifier = "pin-verifier",
                managementToken = "management-secret",
            ),
        )
        credentials.clearEnrollment()

        assertFalse(credentials.hasRefreshToken())
        assertEquals("management-secret", credentials.managementToken())
        credentials.clearManagementToken()
    }

    @Test
    @Suppress("DEPRECATION")
    fun testMergedApplicationDisablesBackup() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val applicationInfo = context.packageManager.getApplicationInfo(
            context.packageName,
            0,
        )
        assertEquals(0, applicationInfo.flags and ApplicationInfo.FLAG_ALLOW_BACKUP)
    }

    @Test
    @Suppress("DEPRECATION")
    fun testAppDoesNotRequestBootCompletedOrAutoLaunchAfterReboot() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val packageInfo = context.packageManager.getPackageInfo(
            context.packageName,
            PackageManager.GET_PERMISSIONS,
        )
        assertFalse(
            packageInfo.requestedPermissions?.contains(Manifest.permission.RECEIVE_BOOT_COMPLETED) == true,
        )
        listOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_LOCKED_BOOT_COMPLETED,
            Intent.ACTION_POWER_CONNECTED,
            Intent.ACTION_POWER_DISCONNECTED,
        ).forEach { action ->
            val receivers = context.packageManager.queryBroadcastReceivers(
                Intent(action).setPackage(context.packageName),
                0,
            )
            assertTrue("Unexpected static receiver for $action", receivers.isEmpty())
        }
    }

    @Test
    @Suppress("DEPRECATION")
    fun testAdminRelockReceiverIsNotExported() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val receiver = context.packageManager.getReceiverInfo(
            ComponentName(context, AdminRelockReceiver::class.java),
            0,
        )
        assertFalse(receiver.exported)
        assertFalse(receiver.directBootAware)
    }

    @Test
    fun testRequiredKioskPolicyBlocksManualDateAndTimeChanges() {
        assertTrue(
            KioskPolicyManager.requiredUserRestrictions.contains(
                UserManager.DISALLOW_CONFIG_DATE_TIME,
            ),
        )
        assertFalse(
            KioskPolicyManager.requiredUserRestrictions.contains(
                UserManager.DISALLOW_USB_FILE_TRANSFER,
            ),
        )
    }

    @Test
    fun testBatchQueueAndPlaybackCheckpointClearAreAtomic() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = PlayerStore(context)
        val batchId = UUID.randomUUID().toString()
        store.putState("current_playback", "checkpoint")
        store.putState("loop_results", "results")
        store.putState("loop_started_at", "start")

        store.enqueueBatchAndClearPlaybackState(
            batch = JSONObject().put("id", batchId).put("events", emptyList<String>()),
            maxBytes = 500L * 1024 * 1024,
            minimumFreeBytes = 0,
            recordedAt = "2026-08-01T00:00:00Z",
        )

        assertEquals("", store.state("current_playback"))
        assertEquals("", store.state("loop_results"))
        assertEquals("", store.state("loop_started_at"))
        assertEquals(batchId, store.pendingBatch(batchId)?.getString("id"))
        store.acknowledgeBatch(batchId)
    }

    @Test
    fun testEvidenceQueueCapAppliesWithoutLowStoragePressure() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = PlayerStore(context)
        val batchId = UUID.randomUUID().toString()

        store.enqueueBatch(
            batch = JSONObject().put("id", batchId).put("padding", "x".repeat(128)),
            maxBytes = 1,
            minimumFreeBytes = 0,
            recordedAt = "2026-08-01T00:00:00Z",
        )

        assertEquals(null, store.pendingBatch(batchId))
    }

    @Test
    fun testCorruptBatchIsDroppedWithoutStarvingLaterEvidence() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = PlayerStore(context)
        val corruptId = UUID.randomUUID().toString()
        val validId = UUID.randomUUID().toString()
        val recordedAt = "2040-08-01T00:00:00Z"
        store.writableDatabase.insertOrThrow(
            "pending_batches",
            null,
            ContentValues().apply {
                put("id", corruptId)
                put("payload", "not-json")
                put("payload_bytes", 8)
                put("created_at", System.currentTimeMillis() - 1)
            },
        )
        store.enqueueBatch(
            batch = JSONObject().put("id", validId).put("events", emptyList<String>()),
            maxBytes = 500L * 1024 * 1024,
            minimumFreeBytes = 0,
        )

        assertEquals(validId, store.oldestPendingBatch(recordedAt)?.first)
        assertEquals(null, store.pendingBatch(corruptId))
        val loss = store.writableDatabase.query(
            "pending_operational_events",
            arrayOf("payload"),
            null,
            null,
            null,
            null,
            null,
        ).use { cursor ->
            generateSequence {
                if (cursor.moveToNext()) JSONObject(cursor.getString(0)) else null
            }.first { event ->
                event.optString("kind") == "forced_queue_loss" &&
                    event.optString("recorded_at") == recordedAt
            }
        }
        assertEquals(1, loss.getJSONObject("details").getInt("removed_batches"))
        assertEquals(8, loss.getJSONObject("details").getInt("estimated_removed_bytes"))
        assertEquals(0, loss.getJSONObject("details").getInt("target_removed_bytes"))
        store.acknowledgeBatch(validId)
    }

    @Test
    fun testManifestPolicyRejectsUnsafeUrlAndOversizedQueue() {
        val playlistId = UUID.randomUUID().toString()
        val entryId = UUID.randomUUID().toString()
        val mediaId = UUID.randomUUID().toString()
        val manifest = JSONObject()
            .put("id", playlistId)
            .put("version", 1)
            .put("urgent", false)
            .put("required_app_version", "1.0.0")
            .put("media_origin", "MEDIA.EXAMPLE.TEST")
            .put("starts_at", "2026-08-01T00:00:00Z")
            .put("ends_at", "2026-08-08T00:00:00Z")
            .put("media_cache_bytes", 10L * 1024 * 1024)
            .put("event_queue_bytes", 1024L)
            .put("minimum_free_bytes", 0L)
            .put("sync_timezone", "Asia/Kuala_Lumpur")
            .put("daily_sync_local_time", "00:00:00")
            .put(
                "items",
                JSONArray().put(
                    JSONObject()
                        .put("entry_id", entryId)
                        .put("media_id", mediaId)
                        .put("kind", "image")
                        .put("sha256", "a".repeat(64))
                        .put("size_bytes", 1L)
                        .put("duration_ms", 10_000L)
                        .put("download_url", "https://media.example.test/validated/$mediaId"),
                ),
            )

        assertEquals(ManifestIdentity(playlistId, 1), ManifestPolicy.validate(manifest, "1.0.0"))
        manifest.getJSONArray("items").getJSONObject(0).put("download_url", "file:///data/local/tmp/a")
        assertEquals(null, ManifestPolicy.validate(manifest, "1.0.0"))
        manifest.getJSONArray("items").getJSONObject(0)
            .put("download_url", "https://127.0.0.1/validated/$mediaId")
        assertEquals(null, ManifestPolicy.validate(manifest, "1.0.0"))
        manifest.getJSONArray("items").getJSONObject(0)
            .put("download_url", "https://other.example.test/validated/$mediaId")
        assertEquals(null, ManifestPolicy.validate(manifest, "1.0.0"))
        manifest.getJSONArray("items").getJSONObject(0)
            .put("download_url", "https://media.example.test/${"x".repeat(8 * 1024)}")
        assertEquals(null, ManifestPolicy.validate(manifest, "1.0.0"))
        manifest.getJSONArray("items").getJSONObject(0)
            .put("download_url", "https://media.example.test/validated/$mediaId")
        manifest.remove("media_origin")
        assertEquals(null, ManifestPolicy.validate(manifest, "1.0.0"))
        manifest.put("media_origin", "media.example.test")
        manifest.put("event_queue_bytes", ManifestPolicy.MAX_EVENT_QUEUE_BYTES + 1)
        assertEquals(null, ManifestPolicy.validate(manifest, "1.0.0"))
    }

    @Test
    fun testPlannedShutdownMarkerAndOperationalEventAreDurableTogether() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = PlayerStore(context)
        store.clearPlannedShutdownMarker()
        store.clearRecentOrderlyPlannedShutdownMarker()
        val marker = PlannedShutdownMarker(
            id = UUID.randomUUID().toString(),
            preparedAtEpochMs = 1_000,
        )
        val event = JSONObject()
            .put("id", marker.id)
            .put("kind", "planned_shutdown")
            .put("recorded_at", "2026-08-01T00:00:00Z")
            .put("details", JSONObject())

        assertTrue(store.preparePlannedShutdown(marker, event))
        assertEquals(marker, store.plannedShutdownMarker())
        val queued = requireNotNull(store.pendingOperationalEvent(marker.id))
        assertEquals("planned_shutdown", queued.getString("kind"))
        assertEquals(0, queued.getJSONObject("details").length())
        assertFalse(store.preparePlannedShutdown(marker, event))

        assertTrue(store.markPlannedShutdownOrderly(1_500))
        assertEquals(1_500L, store.plannedShutdownMarker()?.orderlyShutdownAtEpochMs)
        store.acknowledgeOperationalEvent(marker.id)
        store.clearPlannedShutdownMarker()
        store.clearRecentOrderlyPlannedShutdownMarker()
    }

    @Test
    fun testResumeRetainsOrderlyMarkerForMatchingExitHistoryWithoutBlockingPlayback() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = PlayerStore(context)
        store.clearPlannedShutdownMarker()
        store.clearRecentOrderlyPlannedShutdownMarker()
        val marker = PlannedShutdownMarker(
            id = UUID.randomUUID().toString(),
            preparedAtEpochMs = 1_000,
        )
        val event = JSONObject()
            .put("id", marker.id)
            .put("kind", "planned_shutdown")
            .put("recorded_at", "2026-08-01T00:00:00Z")
            .put("details", JSONObject())

        assertTrue(store.preparePlannedShutdown(marker, event))
        assertTrue(store.markPlannedShutdownOrderly(2_000))
        // This represents the explicit Resume DUDU confirmation. It must
        // clear the active gate, but not lose the orderly-exit evidence before
        // the next manifest sync reads Android's exit history.
        store.clearPlannedShutdownMarker()

        assertFalse(store.hasPlannedShutdownMarker())
        assertTrue(ShutdownPreparationPolicy.shouldResumeAutomatically(false))
        val retained = store.recentOrderlyPlannedShutdownMarker(2_001)
        assertEquals(2_000L, retained?.orderlyShutdownAtEpochMs)
        assertTrue((retained?.resumedAtEpochMs ?: 0L) >= 2_000L)
        assertTrue(
            retained != null &&
                ShutdownPreparationPolicy.shouldSuppressAbnormalExit(
                    marker = retained,
                    exitTimestampMs = 2_001,
                    nowEpochMs = 2_001,
                ),
        )
        assertEquals(
            null,
            store.recentOrderlyPlannedShutdownMarker(
                2_000 + ShutdownPreparationPolicy.ABNORMAL_EXIT_SUPPRESSION_WINDOW_MS + 1,
            ),
        )
        store.acknowledgeOperationalEvent(marker.id)
        store.clearRecentOrderlyPlannedShutdownMarker()
    }

    @Test
    fun testUnanchoredPlannedShutdownEventRebasesBeforeUpload() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val store = PlayerStore(context)
        store.clearPlannedShutdownMarker()
        store.clearRecentOrderlyPlannedShutdownMarker()
        val marker = PlannedShutdownMarker(
            id = UUID.randomUUID().toString(),
            preparedAtEpochMs = 1_000,
            requiresTrustedTimestampRebase = true,
        )
        val event = JSONObject()
            .put("id", marker.id)
            .put("kind", "planned_shutdown")
            .put("recorded_at", "2099-08-01T00:00:00Z")
            .put("details", JSONObject())

        assertTrue(store.preparePlannedShutdown(marker, event))
        assertEquals(
            "2099-08-01T00:00:00Z",
            store.pendingOperationalEvent(marker.id)?.getString("recorded_at"),
        )
        assertEquals(1, store.rebaseUnanchoredPlannedShutdownEvents("2026-08-01T00:00:00Z"))
        val rebased = requireNotNull(store.pendingOperationalEvent(marker.id))
        assertEquals("2026-08-01T00:00:00Z", rebased.getString("recorded_at"))
        assertEquals(0, rebased.getJSONObject("details").length())
        assertEquals(0, store.rebaseUnanchoredPlannedShutdownEvents("2026-08-01T00:01:00Z"))
        store.acknowledgeOperationalEvent(marker.id)
        store.clearPlannedShutdownMarker()
        store.clearRecentOrderlyPlannedShutdownMarker()
    }
}

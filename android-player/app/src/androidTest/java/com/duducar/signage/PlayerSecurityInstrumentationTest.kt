package com.duducar.signage

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ApplicationInfo
import android.os.UserManager
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
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
        assertTrue(store.pendingBatches().any { it.first == batchId })
        store.acknowledgeBatch(batchId)
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
        val queued = store.pendingOperationalEvents().first { it.first == marker.id }
        assertEquals("planned_shutdown", queued.second.getString("kind"))
        assertEquals(0, queued.second.getJSONObject("details").length())
        assertFalse(store.preparePlannedShutdown(marker, event))

        assertTrue(store.markPlannedShutdownOrderly(1_500))
        assertEquals(1_500, store.plannedShutdownMarker()?.orderlyShutdownAtEpochMs)
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
        assertEquals(2_000, retained?.orderlyShutdownAtEpochMs)
        assertTrue((retained?.resumedAtEpochMs ?: 0) >= 2_000)
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
            store.pendingOperationalEvents().first { it.first == marker.id }
                .second.getString("recorded_at"),
        )
        assertEquals(1, store.rebaseUnanchoredPlannedShutdownEvents("2026-08-01T00:00:00Z"))
        val rebased = store.pendingOperationalEvents().first { it.first == marker.id }.second
        assertEquals("2026-08-01T00:00:00Z", rebased.getString("recorded_at"))
        assertEquals(0, rebased.getJSONObject("details").length())
        assertEquals(0, store.rebaseUnanchoredPlannedShutdownEvents("2026-08-01T00:01:00Z"))
        store.acknowledgeOperationalEvent(marker.id)
        store.clearPlannedShutdownMarker()
        store.clearRecentOrderlyPlannedShutdownMarker()
    }
}

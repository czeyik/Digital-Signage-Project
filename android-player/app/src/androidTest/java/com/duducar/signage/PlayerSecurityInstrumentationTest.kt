package com.duducar.signage

import android.content.ComponentName
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
    fun testBootReceiverIsNotExported() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val receiver = context.packageManager.getReceiverInfo(
            ComponentName(context, BootReceiver::class.java),
            0,
        )
        assertFalse(receiver.exported)
        assertTrue(receiver.directBootAware)
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
}

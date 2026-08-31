package com.duducar.signage

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.SystemClock

/**
 * Best-effort process-death recovery for periodic operations. These inexact
 * alarms intentionally do not survive reboot: the product forbids a boot
 * receiver and a prepared shutdown must never resume advertising by itself.
 */
class OperationsScheduler(context: Context) {
    private val applicationContext = context.applicationContext
    private val alarmManager = applicationContext.getSystemService(AlarmManager::class.java)

    fun scheduleHeartbeat() = schedule(ACTION_HEARTBEAT, HEARTBEAT_INTERVAL_MS)

    fun scheduleSync() = schedule(ACTION_SYNC, SYNC_INTERVAL_MS)

    fun scheduleManagement() = schedule(ACTION_MANAGEMENT, MANAGEMENT_INTERVAL_MS)

    fun schedulePlaylistTransition(delayMs: Long): Boolean = try {
        alarmManager.setExactAndAllowWhileIdle(
            AlarmManager.ELAPSED_REALTIME_WAKEUP,
            SystemClock.elapsedRealtime() + delayMs.coerceAtLeast(0),
            pendingIntent(ACTION_PLAYLIST_TRANSITION, PLAYLIST_TRANSITION_REQUEST_CODE),
        )
        true
    } catch (_: SecurityException) {
        false
    }

    fun cancel() {
        cancelPlayback()
        cancelManagement()
    }

    fun cancelPlayback() {
        alarmManager.cancel(pendingIntent(ACTION_HEARTBEAT, HEARTBEAT_REQUEST_CODE))
        alarmManager.cancel(pendingIntent(ACTION_SYNC, SYNC_REQUEST_CODE))
        alarmManager.cancel(
            pendingIntent(ACTION_PLAYLIST_TRANSITION, PLAYLIST_TRANSITION_REQUEST_CODE),
        )
    }

    fun cancelManagement() {
        alarmManager.cancel(pendingIntent(ACTION_MANAGEMENT, MANAGEMENT_REQUEST_CODE))
    }

    fun cancelPlaylistTransition() {
        alarmManager.cancel(
            pendingIntent(ACTION_PLAYLIST_TRANSITION, PLAYLIST_TRANSITION_REQUEST_CODE),
        )
    }

    private fun schedule(action: String, delayMs: Long) {
        alarmManager.setAndAllowWhileIdle(
            AlarmManager.ELAPSED_REALTIME_WAKEUP,
            SystemClock.elapsedRealtime() + delayMs,
            pendingIntent(
                action,
                when (action) {
                    ACTION_HEARTBEAT -> HEARTBEAT_REQUEST_CODE
                    ACTION_SYNC -> SYNC_REQUEST_CODE
                    else -> MANAGEMENT_REQUEST_CODE
                },
            ),
        )
    }

    private fun pendingIntent(action: String, requestCode: Int): PendingIntent = PendingIntent.getActivity(
        applicationContext,
        requestCode,
        Intent(applicationContext, MainActivity::class.java).setAction(action).apply {
            addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        },
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    companion object {
        const val ACTION_HEARTBEAT = "com.duducar.signage.action.PERIODIC_HEARTBEAT"
        const val ACTION_SYNC = "com.duducar.signage.action.PERIODIC_SYNC"
        const val ACTION_MANAGEMENT = "com.duducar.signage.action.PERIODIC_MANAGEMENT"
        const val ACTION_PLAYLIST_TRANSITION =
            "com.duducar.signage.action.PLAYLIST_TRANSITION"
        const val HEARTBEAT_INTERVAL_MS = 30L * 60 * 1000
        const val SYNC_INTERVAL_MS = 60L * 60 * 1000
        const val MANAGEMENT_INTERVAL_MS = 60L * 1000
        private const val HEARTBEAT_REQUEST_CODE = 7105
        private const val SYNC_REQUEST_CODE = 7106
        private const val MANAGEMENT_REQUEST_CODE = 7107
        private const val PLAYLIST_TRANSITION_REQUEST_CODE = 7108
    }
}

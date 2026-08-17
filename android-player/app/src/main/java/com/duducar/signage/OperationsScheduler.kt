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

    fun cancel() {
        alarmManager.cancel(pendingIntent(ACTION_HEARTBEAT, HEARTBEAT_REQUEST_CODE))
        alarmManager.cancel(pendingIntent(ACTION_SYNC, SYNC_REQUEST_CODE))
    }

    private fun schedule(action: String, delayMs: Long) {
        alarmManager.setAndAllowWhileIdle(
            AlarmManager.ELAPSED_REALTIME_WAKEUP,
            SystemClock.elapsedRealtime() + delayMs,
            pendingIntent(
                action,
                if (action == ACTION_HEARTBEAT) HEARTBEAT_REQUEST_CODE else SYNC_REQUEST_CODE,
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
        const val HEARTBEAT_INTERVAL_MS = 30L * 60 * 1000
        const val SYNC_INTERVAL_MS = 60L * 60 * 1000
        private const val HEARTBEAT_REQUEST_CODE = 7105
        private const val SYNC_REQUEST_CODE = 7106
    }
}

package com.duducar.signage

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.SystemClock

class AdminRelockScheduler(context: Context) {
    private val applicationContext = context.applicationContext
    private val alarmManager = applicationContext.getSystemService(AlarmManager::class.java)

    fun mayScheduleExactAlarm(): Boolean = alarmManager.canScheduleExactAlarms()

    fun schedule(delayMs: Long): Boolean {
        if (delayMs <= 0 || !mayScheduleExactAlarm()) return false
        return try {
            alarmManager.setExactAndAllowWhileIdle(
                AlarmManager.ELAPSED_REALTIME_WAKEUP,
                SystemClock.elapsedRealtime() + delayMs,
                pendingIntent(),
            )
            true
        } catch (_: SecurityException) {
            false
        }
    }

    fun cancel() {
        alarmManager.cancel(pendingIntent())
    }

    private fun pendingIntent(): PendingIntent = PendingIntent.getBroadcast(
        applicationContext,
        REQUEST_CODE,
        Intent(applicationContext, AdminRelockReceiver::class.java).setAction(ACTION_RELOCK),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
    )

    companion object {
        const val ACTION_RELOCK = "com.duducar.signage.action.RELOCK_ADMIN_SESSION"
        private const val REQUEST_CODE = 7104
    }
}

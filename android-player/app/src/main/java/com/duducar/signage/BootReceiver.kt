package com.duducar.signage

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.UserManager

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action !in acceptedActions) return
        val userManager = context.getSystemService(UserManager::class.java)
        // Credential-protected player state is intentionally unavailable at
        // locked boot. BOOT_COMPLETED will launch the player after first unlock.
        if (!userManager.isUserUnlocked) return
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            CredentialStore(context).endAdminSession()
            AdminRelockScheduler(context).cancel()
        }
        context.startActivity(
            Intent(context, MainActivity::class.java).apply {
                addFlags(
                    Intent.FLAG_ACTIVITY_NEW_TASK or
                        Intent.FLAG_ACTIVITY_CLEAR_TOP or
                        Intent.FLAG_ACTIVITY_SINGLE_TOP,
                )
            },
        )
    }

    companion object {
        private val acceptedActions = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_LOCKED_BOOT_COMPLETED,
            Intent.ACTION_POWER_CONNECTED,
        )
    }
}

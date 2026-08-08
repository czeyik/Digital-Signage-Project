package com.duducar.signage

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

class AdminRelockReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != AdminRelockScheduler.ACTION_RELOCK) return
        CredentialStore(context).endAdminSession()
        KioskPolicyManager(context).applyLockedPolicies()
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
}

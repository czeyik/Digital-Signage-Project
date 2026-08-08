package com.duducar.signage

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.UserManager

class KioskPolicyManager(private val context: Context) {
    private val manager = context.getSystemService(DevicePolicyManager::class.java)
    private val admin = ComponentName(context, KioskDeviceAdminReceiver::class.java)

    fun isDeviceOwner(): Boolean = manager.isDeviceOwnerApp(context.packageName)

    fun applyLockedPolicies(): Boolean {
        if (!isDeviceOwner()) return false
        return try {
            manager.setLockTaskPackages(admin, arrayOf(context.packageName))
            manager.setLockTaskFeatures(admin, DevicePolicyManager.LOCK_TASK_FEATURE_NONE)
            manager.setScreenCaptureDisabled(admin, true)
            if (!manager.setStatusBarDisabled(admin, true)) return false
            if (!manager.setKeyguardDisabled(admin, true)) return false
            requiredUserRestrictions.forEach { restriction ->
                manager.addUserRestriction(admin, restriction)
            }
            manager.addPersistentPreferredActivity(
                admin,
                IntentFilter(Intent.ACTION_MAIN).apply {
                    addCategory(Intent.CATEGORY_HOME)
                    addCategory(Intent.CATEGORY_DEFAULT)
                },
                ComponentName(context, MainActivity::class.java),
            )
            manager.isLockTaskPermitted(context.packageName)
        } catch (_: SecurityException) {
            false
        } catch (_: IllegalArgumentException) {
            false
        }
    }

    fun relaxForAdminSession(): Boolean {
        if (!isDeviceOwner()) return false
        return try {
            // Keep HOME ownership, screen-capture prevention, and the safety
            // restrictions in place. The bounded session only exposes system
            // UI so staff can inspect the tablet or sideload an update.
            manager.setStatusBarDisabled(admin, false)
        } catch (_: SecurityException) {
            false
        }
    }

    companion object {
        val requiredUserRestrictions = listOf(
            UserManager.DISALLOW_SAFE_BOOT,
            UserManager.DISALLOW_ADD_USER,
            UserManager.DISALLOW_REMOVE_USER,
            UserManager.DISALLOW_APPS_CONTROL,
            UserManager.DISALLOW_CREATE_WINDOWS,
            UserManager.DISALLOW_CONFIG_DATE_TIME,
            UserManager.DISALLOW_MOUNT_PHYSICAL_MEDIA,
            UserManager.DISALLOW_USB_FILE_TRANSFER,
        )
    }
}

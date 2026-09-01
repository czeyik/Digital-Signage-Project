package com.duducar.signage

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class CredentialStore(context: Context) {
    private val preferences =
        context.getSharedPreferences("device_credentials", Context.MODE_PRIVATE)
    private val alias = "duducar-device-refresh"

    fun hasRefreshToken(): Boolean = refreshToken() != null

    fun saveEnrollmentCredentials(
        refreshToken: String,
        kioskPinVerifier: String,
        managementToken: String,
    ): Boolean {
        if (refreshToken.isBlank() || kioskPinVerifier.isBlank() || managementToken.isBlank()) {
            return false
        }
        val refresh = encrypt(refreshToken)
        val management = encrypt(managementToken)
        val committed = preferences.edit()
            .putString(
                "refresh_ciphertext",
                refresh.first,
            )
            .putString("refresh_iv", refresh.second)
            .putString("management_ciphertext", management.first)
            .putString("management_iv", management.second)
            .putString("kiosk_pin_verifier", kioskPinVerifier)
            .commit()
        if (!committed) clearEnrollment()
        return committed
    }

    fun saveManagementToken(token: String): Boolean {
        if (token.isBlank()) return false
        val encrypted = encrypt(token)
        return preferences.edit()
            .putString("management_ciphertext", encrypted.first)
            .putString("management_iv", encrypted.second)
            .commit()
    }

    fun hasManagementToken(): Boolean = managementToken() != null

    fun clearManagementToken() {
        preferences.edit()
            .remove("management_ciphertext")
            .remove("management_iv")
            .commit()
    }

    fun saveKioskPinVerifier(verifier: String) {
        if (verifier.isNotBlank()) {
            preferences.edit().putString("kiosk_pin_verifier", verifier).commit()
        }
    }

    fun clearEnrollment() {
        preferences.edit()
            .remove("refresh_ciphertext")
            .remove("refresh_iv")
            .remove("kiosk_pin_verifier")
            .commit()
    }

    fun verifyKioskPin(pin: String): Boolean {
        val verifier = preferences.getString("kiosk_pin_verifier", "") ?: ""
        return PinVerifier.verify(pin, verifier)
    }

    fun hasKioskPinVerifier(): Boolean =
        !preferences.getString("kiosk_pin_verifier", "").isNullOrBlank()

    fun pinLockoutRemainingMs(nowEpochMs: Long): Long =
        KioskAdminPolicy.remainingLockoutMs(pinThrottleState(), nowEpochMs)

    fun recordFailedPinAttempt(nowEpochMs: Long): Long {
        val state = KioskAdminPolicy.afterFailure(pinThrottleState(), nowEpochMs)
        preferences.edit()
            .putInt("kiosk_pin_failed_attempts", state.failedAttempts)
            .putLong("kiosk_pin_locked_until", state.lockedUntilEpochMs)
            .commit()
        return KioskAdminPolicy.remainingLockoutMs(state, nowEpochMs)
    }

    fun clearPinFailures() {
        preferences.edit()
            .remove("kiosk_pin_failed_attempts")
            .remove("kiosk_pin_locked_until")
            .commit()
    }

    fun beginAdminSession(nowEpochMs: Long, nowElapsedMs: Long) {
        preferences.edit()
            .putLong(
                "kiosk_admin_session_until",
                nowEpochMs + KioskAdminPolicy.SESSION_DURATION_MS,
            )
            .putLong(
                "kiosk_admin_session_elapsed_until",
                nowElapsedMs + KioskAdminPolicy.SESSION_DURATION_MS,
            )
            .commit()
    }

    fun adminSessionRemainingMs(nowEpochMs: Long, nowElapsedMs: Long): Long =
        KioskAdminPolicy.remainingSessionMs(
            preferences.getLong("kiosk_admin_session_until", 0L),
            preferences.getLong("kiosk_admin_session_elapsed_until", 0L),
            nowEpochMs,
            nowElapsedMs,
        )

    fun endAdminSession() {
        preferences.edit()
            .remove("kiosk_admin_session_until")
            .remove("kiosk_admin_session_elapsed_until")
            .commit()
    }

    fun refreshToken(): String? = runCatching {
        val ciphertext = preferences.getString("refresh_ciphertext", null) ?: return null
        val iv = preferences.getString("refresh_iv", null) ?: return null
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            getOrCreateKey(),
            GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)),
        )
        String(cipher.doFinal(Base64.decode(ciphertext, Base64.NO_WRAP)), Charsets.UTF_8)
    }.getOrElse {
        // A factory reset, keystore invalidation, or corrupt preference is not
        // a retryable authentication error. Return to deliberate enrollment.
        clearEnrollment()
        null
    }

    fun managementToken(): String? = decrypt("management_ciphertext", "management_iv")

    private fun encrypt(value: String): Pair<String, String> {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey())
        return Base64.encodeToString(
            cipher.doFinal(value.toByteArray(Charsets.UTF_8)),
            Base64.NO_WRAP,
        ) to Base64.encodeToString(cipher.iv, Base64.NO_WRAP)
    }

    private fun decrypt(ciphertextKey: String, ivKey: String): String? = runCatching {
        val ciphertext = preferences.getString(ciphertextKey, null) ?: return null
        val iv = preferences.getString(ivKey, null) ?: return null
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(
            Cipher.DECRYPT_MODE,
            getOrCreateKey(),
            GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)),
        )
        String(cipher.doFinal(Base64.decode(ciphertext, Base64.NO_WRAP)), Charsets.UTF_8)
    }.getOrNull()

    private fun getOrCreateKey(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(
                KeyGenParameterSpec.Builder(
                    alias,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .build(),
            )
            generateKey()
        }
    }

    private fun pinThrottleState(): PinThrottleState = PinThrottleState(
        failedAttempts = preferences.getInt("kiosk_pin_failed_attempts", 0),
        lockedUntilEpochMs = preferences.getLong("kiosk_pin_locked_until", 0L),
    )
}

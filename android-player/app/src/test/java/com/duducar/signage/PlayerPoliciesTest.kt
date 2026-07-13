package com.duducar.signage

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

class PlayerPoliciesTest {
    @Test
    fun cachePolicyRejectsOversizedOrUnsafeReplacement() {
        assertFalse(StoragePolicy.canStage(11_000, 0, 11_000, 20_000, 10_000, 2_000))
        assertFalse(StoragePolicy.canStage(8_000, 0, 8_000, 9_000, 10_000, 2_000))
        assertFalse(StoragePolicy.canStage(8_000, 6_000, 5_000, 20_000, 10_000, 2_000))
        assertTrue(StoragePolicy.canStage(8_000, 4_000, 4_000, 9_000, 10_000, 2_000))
    }

    @Test
    fun queueLossRequiresBothAnOversizedQueueAndCriticalFreeSpace() {
        assertFalse(StoragePolicy.shouldForceQueueLoss(400, 100, 500, 200))
        assertFalse(StoragePolicy.shouldForceQueueLoss(600, 300, 500, 200))
        assertTrue(StoragePolicy.shouldForceQueueLoss(600, 100, 500, 200))
    }

    @Test
    fun pinVerifierAcceptsOnlyTheConfiguredPin() {
        val salt = ByteArray(16) { it.toByte() }
        val iterations = 10_000
        val expected = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
            .generateSecret(PBEKeySpec("123456".toCharArray(), salt, iterations, 256))
            .encoded
            .joinToString("") { "%02x".format(it) }
        val verifier = listOf(
            "pbkdf2_sha256",
            iterations.toString(),
            salt.toHex(),
            expected,
        ).joinToString("$")

        assertTrue(PinVerifier.verify("123456", verifier))
        assertFalse(PinVerifier.verify("654321", verifier))
        assertFalse(PinVerifier.verify("123456", "invalid"))
    }

    private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
}

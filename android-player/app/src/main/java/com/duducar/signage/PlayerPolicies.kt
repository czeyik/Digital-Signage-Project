package com.duducar.signage

import java.security.MessageDigest
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

object StoragePolicy {
    fun canStage(
        requiredBytes: Long,
        cachedBytes: Long,
        downloadBytes: Long,
        usableBytes: Long,
        cacheLimitBytes: Long,
        minimumFreeBytes: Long,
    ): Boolean {
        if (requiredBytes < 0 || requiredBytes > cacheLimitBytes) return false
        if (downloadBytes < 0 || cachedBytes + downloadBytes > cacheLimitBytes) {
            return false
        }
        return usableBytes - downloadBytes >= minimumFreeBytes
    }

    fun shouldForceQueueLoss(
        queueBytes: Long,
        usableBytes: Long,
        maxQueueBytes: Long,
        minimumFreeBytes: Long,
    ): Boolean = queueBytes > maxQueueBytes && usableBytes < minimumFreeBytes
}

object PinVerifier {
    fun verify(pin: String, verifier: String): Boolean {
        val parts = verifier.split("$")
        if (parts.size != 4 || parts[0] != "pbkdf2_sha256") return false
        return try {
            val iterations = parts[1].toInt()
            val salt = parts[2].chunked(2).map { it.toInt(16).toByte() }.toByteArray()
            val expected = parts[3]
            val spec = PBEKeySpec(pin.toCharArray(), salt, iterations, expected.length * 4)
            val actual = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256")
                .generateSecret(spec)
                .encoded
                .joinToString("") { "%02x".format(it) }
            MessageDigest.isEqual(actual.toByteArray(), expected.toByteArray())
        } catch (_: Exception) {
            false
        }
    }
}

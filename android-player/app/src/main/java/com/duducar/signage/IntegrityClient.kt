package com.duducar.signage

import android.content.Context
import com.google.android.play.core.integrity.IntegrityManagerFactory
import com.google.android.play.core.integrity.StandardIntegrityManager
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class IntegrityClient(context: Context) {
    private val manager = IntegrityManagerFactory.createStandard(context)

    fun token(projectNumber: Long, requestHash: String): String {
        require(projectNumber > 0) { "Play Integrity project number is not configured." }
        val prepareRequest =
            StandardIntegrityManager.PrepareIntegrityTokenRequest.builder()
                .setCloudProjectNumber(projectNumber)
                .build()
        val providerLatch = CountDownLatch(1)
        var provider: StandardIntegrityManager.StandardIntegrityTokenProvider? = null
        var failure: Exception? = null
        manager.prepareIntegrityToken(prepareRequest)
            .addOnSuccessListener {
                provider = it
                providerLatch.countDown()
            }
            .addOnFailureListener {
                failure = it as? Exception ?: IllegalStateException("Integrity preparation failed.")
                providerLatch.countDown()
            }
        if (!providerLatch.await(30, TimeUnit.SECONDS)) {
            throw IllegalStateException("Integrity preparation timed out.")
        }
        failure?.let { throw it }

        val tokenLatch = CountDownLatch(1)
        var token: String? = null
        provider!!.request(
            StandardIntegrityManager.StandardIntegrityTokenRequest.builder()
                .setRequestHash(requestHash)
                .build(),
        ).addOnSuccessListener {
            token = it.token()
            tokenLatch.countDown()
        }.addOnFailureListener {
            failure = it as? Exception ?: IllegalStateException("Integrity request failed.")
            tokenLatch.countDown()
        }
        if (!tokenLatch.await(30, TimeUnit.SECONDS)) {
            throw IllegalStateException("Integrity request timed out.")
        }
        failure?.let { throw it }
        return requireNotNull(token)
    }
}

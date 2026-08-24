package com.duducar.signage

import android.os.Build
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL

class ApiClient(private val credentials: CredentialStore) {
    private var accessToken: String? = null

    fun clearAccessToken() {
        accessToken = null
    }

    fun enrollmentChallenge(code: String, androidId: String): JSONObject {
        val body = JSONObject()
            .put("code", code)
            .put("android_id", androidId)
            .put("android_version", Build.VERSION.RELEASE)
            .put("app_version", BuildConfig.VERSION_NAME)
            .put("hardware_model", Build.MODEL)
            .put("firmware_version", Build.DISPLAY)
            .put("security_patch_level", Build.VERSION.SECURITY_PATCH)
        return request("devices/enrollment-challenge/", "POST", body, authenticated = false)
    }

    fun enroll(challengeId: String, integrityToken: String): JSONObject {
        return saveEnrollment(
            request(
                "devices/enroll/",
                "POST",
                JSONObject()
                    .put("challenge_id", challengeId)
                    .put("integrity_token", integrityToken),
                authenticated = false,
            ),
        )
    }

    fun enrollDevelopment(code: String, androidId: String): JSONObject {
        return saveEnrollment(
            request(
                "devices/enroll/",
                "POST",
                JSONObject()
                    .put("code", code)
                    .put("android_id", androidId)
                    .put("android_version", Build.VERSION.RELEASE)
                    .put("app_version", BuildConfig.VERSION_NAME),
                authenticated = false,
            ),
        )
    }

    private fun saveEnrollment(response: JSONObject): JSONObject {
        if (!credentials.saveEnrollmentCredentials(
                refreshToken = response.getString("refresh_token"),
                kioskPinVerifier = response.getString("kiosk_pin_verifier"),
            )
        ) {
            throw CredentialPersistenceException()
        }
        accessToken = response.getString("access_token")
        return response
    }

    fun manifest(): JSONObject = authenticatedRequest("devices/sync/", "GET").also {
        credentials.saveKioskPinVerifier(it.optString("kiosk_pin_verifier"))
    }

    fun heartbeat(body: JSONObject): JSONObject =
        authenticatedRequest("devices/heartbeat/", "POST", body)

    fun uploadBatch(body: JSONObject): JSONObject =
        authenticatedRequest(
            "devices/playback-batches/",
            "POST",
            body,
            compressBody = true,
        )

    fun uploadOperationalEvent(body: JSONObject): JSONObject =
        authenticatedRequest("devices/operational-events/", "POST", body)

    private fun authenticatedRequest(
        path: String,
        method: String,
        body: JSONObject? = null,
        compressBody: Boolean = false,
    ): JSONObject {
        if (accessToken == null) refreshAccessToken()
        return try {
            request(path, method, body, authenticated = true, compressBody = compressBody)
        } catch (_: UnauthorizedException) {
            accessToken = null
            refreshAccessToken()
            try {
                request(path, method, body, authenticated = true, compressBody = compressBody)
            } catch (_: UnauthorizedException) {
                credentials.clearEnrollment()
                accessToken = null
                throw CredentialRejectedException()
            }
        }
    }

    private fun refreshAccessToken() {
        val refresh = credentials.refreshToken() ?: throw CredentialRejectedException()
        val response = try {
            request(
                "devices/token/",
                "POST",
                JSONObject().put("refresh_token", refresh),
                authenticated = false,
            )
        } catch (_: UnauthorizedException) {
            credentials.clearEnrollment()
            accessToken = null
            throw CredentialRejectedException()
        }
        accessToken = response.getString("access_token")
    }

    private fun request(
        path: String,
        method: String,
        body: JSONObject? = null,
        authenticated: Boolean,
        compressBody: Boolean = false,
    ): JSONObject {
        val connection = URL(BuildConfig.API_BASE_URL + path).openConnection() as HttpURLConnection
        connection.requestMethod = method
        connection.connectTimeout = 15_000
        connection.readTimeout = 30_000
        connection.setRequestProperty("Accept", "application/json")
        connection.setRequestProperty("Content-Type", PlaybackBatchTransport.CONTENT_TYPE)
        if (authenticated) connection.setRequestProperty("Authorization", "Bearer $accessToken")
        if (body != null) {
            connection.doOutput = true
            val payload = if (compressBody) {
                connection.setRequestProperty(
                    "Content-Encoding",
                    PlaybackBatchTransport.CONTENT_ENCODING,
                )
                PlaybackBatchTransport.encodeJson(body.toString())
            } else {
                body.toString().toByteArray(Charsets.UTF_8)
            }
            connection.setFixedLengthStreamingMode(payload.size)
            connection.outputStream.use { it.write(payload) }
        }
        val status = connection.responseCode
        val stream = if (status in 200..299) connection.inputStream else connection.errorStream
        val text = try {
            if (connection.contentLengthLong > ApiResponsePolicy.MAX_BYTES.toLong()) {
                throw ResponseTooLargeException()
            }
            ApiResponsePolicy.readBounded(stream)
        } catch (_: ResponseTooLargeException) {
            if (status == 401) throw UnauthorizedException()
            if (status == 403) throw ForbiddenException()
            throw ApiException(status, "Response body exceeds the safe size limit.")
        }
        if (status == 401) throw UnauthorizedException()
        if (status == 403) throw ForbiddenException(text)
        if (status !in 200..299) throw ApiException(status, text)
        return JSONObject(text)
    }
}

object ApiResponsePolicy {
    const val MAX_BYTES = 1 * 1024 * 1024

    fun readBounded(stream: InputStream?): String {
        if (stream == null) return ""
        stream.use { input ->
            val output = ByteArrayOutputStream()
            val buffer = ByteArray(8 * 1024)
            var total = 0
            while (true) {
                val count = input.read(buffer)
                if (count <= 0) break
                if (count > MAX_BYTES - total) throw ResponseTooLargeException()
                output.write(buffer, 0, count)
                total += count
            }
            return output.toString(Charsets.UTF_8.name())
        }
    }
}

class UnauthorizedException : RuntimeException()
class ForbiddenException(message: String = "") : RuntimeException(message)
class CredentialRejectedException : RuntimeException()
class CredentialPersistenceException : RuntimeException()
class ApiException(val status: Int, message: String) : RuntimeException(message)
private class ResponseTooLargeException : RuntimeException()

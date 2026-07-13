package com.duducar.signage

import android.os.Build
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

class ApiClient(private val credentials: CredentialStore) {
    private var accessToken: String? = null

    fun enrollmentChallenge(code: String, androidId: String): JSONObject {
        val body = JSONObject()
            .put("code", code)
            .put("android_id", androidId)
            .put("android_version", Build.VERSION.RELEASE)
            .put("app_version", BuildConfig.VERSION_NAME)
        return request("devices/enrollment-challenge/", "POST", body, authenticated = false)
    }

    fun enroll(challengeId: String, integrityToken: String): JSONObject {
        val response = request(
            "devices/enroll/",
            "POST",
            JSONObject()
                .put("challenge_id", challengeId)
                .put("integrity_token", integrityToken),
            authenticated = false,
        )
        credentials.saveRefreshToken(response.getString("refresh_token"))
        credentials.saveKioskPinVerifier(response.optString("kiosk_pin_verifier"))
        accessToken = response.getString("access_token")
        return response
    }

    fun manifest(): JSONObject = authenticatedRequest("devices/sync/", "GET").also {
        credentials.saveKioskPinVerifier(it.optString("kiosk_pin_verifier"))
    }

    fun heartbeat(body: JSONObject): JSONObject =
        authenticatedRequest("devices/heartbeat/", "POST", body)

    fun uploadBatch(body: JSONObject): JSONObject =
        authenticatedRequest("devices/playback-batches/", "POST", body)

    fun uploadOperationalEvent(body: JSONObject): JSONObject =
        authenticatedRequest("devices/operational-events/", "POST", body)

    private fun authenticatedRequest(path: String, method: String, body: JSONObject? = null): JSONObject {
        if (accessToken == null) refreshAccessToken()
        return try {
            request(path, method, body, authenticated = true)
        } catch (error: UnauthorizedException) {
            refreshAccessToken()
            request(path, method, body, authenticated = true)
        }
    }

    private fun refreshAccessToken() {
        val refresh = credentials.refreshToken() ?: throw UnauthorizedException()
        val response = request(
            "devices/token/",
            "POST",
            JSONObject().put("refresh_token", refresh),
            authenticated = false,
        )
        accessToken = response.getString("access_token")
    }

    private fun request(
        path: String,
        method: String,
        body: JSONObject? = null,
        authenticated: Boolean,
    ): JSONObject {
        val connection = URL(BuildConfig.API_BASE_URL + path).openConnection() as HttpURLConnection
        connection.requestMethod = method
        connection.connectTimeout = 15_000
        connection.readTimeout = 30_000
        connection.setRequestProperty("Accept", "application/json")
        connection.setRequestProperty("Content-Type", "application/json")
        if (authenticated) connection.setRequestProperty("Authorization", "Bearer $accessToken")
        if (body != null) {
            connection.doOutput = true
            connection.outputStream.use { it.write(body.toString().toByteArray()) }
        }
        val status = connection.responseCode
        if (status == 401 || status == 403) throw UnauthorizedException()
        val stream = if (status in 200..299) connection.inputStream else connection.errorStream
        val text = stream.bufferedReader().use(BufferedReader::readText)
        if (status !in 200..299) throw ApiException(status, text)
        return JSONObject(text)
    }
}

class UnauthorizedException : RuntimeException()
class ApiException(val status: Int, message: String) : RuntimeException(message)

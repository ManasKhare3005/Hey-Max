package com.manaskhare.max

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

class ApiError(val code: Int, message: String) : Exception(message)

/** Max's REST API on the laptop (the same one the dashboard uses), with the pairing token. */
class MaxApi(private val base: String, private val token: String) {

    companion object {
        private val JSON = "application/json".toMediaType()
        private val WAV = "audio/wav".toMediaType()

        val http: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(180, TimeUnit.SECONDS)     // a command may wait for an approval tap
            .writeTimeout(60, TimeUnit.SECONDS)
            .pingInterval(20, TimeUnit.SECONDS)     // keeps the WebSocket alive through NAT/Tailscale
            .build()

        fun from(prefs: Prefs): MaxApi? = if (prefs.paired) MaxApi(prefs.url, prefs.token) else null
    }

    val wsUrl: String
        get() = base.replaceFirst(Regex("^http"), "ws") + "/api/ws?token=" + URLEncoder.encode(token, "UTF-8")

    private fun request(path: String) =
        Request.Builder().url(base + path).header("Authorization", "Bearer $token")

    private suspend fun run(req: Request): ByteArray = withContext(Dispatchers.IO) {
        http.newCall(req).execute().use { res ->
            val body = res.body?.bytes() ?: ByteArray(0)
            if (!res.isSuccessful) {
                val detail = runCatching { JSONObject(String(body)).optString("detail") }.getOrNull()
                throw ApiError(res.code, detail?.ifBlank { null } ?: "HTTP ${res.code}")
            }
            body
        }
    }

    private suspend fun get(path: String) = String(run(request(path).get().build()))
    private suspend fun post(path: String, body: JSONObject = JSONObject()) =
        String(run(request(path).post(body.toString().toRequestBody(JSON)).build()))
    private suspend fun delete(path: String) = String(run(request(path).delete().build()))

    suspend fun ping() = JSONObject(get("/api/ping"))
    suspend fun state() = JSONObject(get("/api/state"))
    suspend fun command(text: String) = JSONObject(post("/api/command", JSONObject().put("text", text))).optString("reply")

    /** Push-to-talk: 16 kHz WAV in, {heard, reply} out. */
    suspend fun voice(wav: ByteArray): Pair<String, String> {
        val res = JSONObject(String(run(request("/api/voice").post(wav.toRequestBody(WAV)).build())))
        return res.optString("heard") to res.optString("reply")
    }

    /** Max's own (Piper) voice as WAV. */
    suspend fun speak(text: String): ByteArray =
        run(request("/api/speak").post(JSONObject().put("text", text).toString().toRequestBody(JSON)).build())

    suspend fun approve(id: Int, approved: Boolean) = post("/api/approvals/$id", JSONObject().put("approved", approved))
    suspend fun control(action: String) = JSONObject(post("/api/control/$action"))

    suspend fun today() = JSONObject(get("/api/today"))
    suspend fun turns(limit: Int = 60) = JSONArray(get("/api/turns?limit=$limit"))
    suspend fun facts() = JSONArray(get("/api/facts"))
    suspend fun addFact(text: String) = post("/api/facts", JSONObject().put("text", text))
    suspend fun deleteFact(id: Int) = delete("/api/facts/$id")
    suspend fun reminders() = JSONArray(get("/api/reminders"))
    suspend fun addReminder(text: String, whenText: String) =
        JSONObject(post("/api/reminders", JSONObject().put("text", text).put("when", whenText)))
    suspend fun cancelReminder(id: Int) = delete("/api/reminders/$id")

    suspend fun notes() = JSONArray(get("/api/notes"))
    suspend fun note(id: Int) = JSONObject(get("/api/notes/$id"))
    suspend fun noteSummary(id: Int, refresh: Boolean = false) =
        JSONObject(post("/api/notes/$id/summary" + if (refresh) "?refresh=true" else "")).optString("summary_md")
    suspend fun notesStatus() = JSONObject(get("/api/notes/status"))
    suspend fun notesStart(kind: String) = post("/api/notes/start", JSONObject().put("kind", kind))
    suspend fun notesStop() = post("/api/notes/stop")
}

fun JSONArray.objects(): List<JSONObject> = (0 until length()).map { getJSONObject(it) }

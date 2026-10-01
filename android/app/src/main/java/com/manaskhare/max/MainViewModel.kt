package com.manaskhare.max

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

enum class Phase { IDLE, LISTENING, THINKING, SPEAKING }

data class Turn(val user: String, val reply: String, val ts: String, val pending: Boolean = false)

class MainViewModel(app: Application) : AndroidViewModel(app) {
    val prefs = Prefs(app)
    private val player = Player(app)
    private var recorder: Recorder? = null

    val phase = MutableStateFlow(Phase.IDLE)
    val level = MutableStateFlow(0f)
    val heard = MutableStateFlow("")
    val reply = MutableStateFlow("")
    val error = MutableStateFlow("")
    val turns = MutableStateFlow<List<Turn>>(emptyList())
    val paired = MutableStateFlow(prefs.paired)

    private fun api(): MaxApi? = MaxApi.from(prefs).also { if (it == null) error.value = "Pair with your laptop first (Settings)." }

    init {
        viewModelScope.launch {           // orb follows the mic level while recording
            while (isActive) {
                level.value = recorder?.level ?: 0f
                delay(50)
            }
        }
        viewModelScope.launch {           // conversation stays current with the laptop
            Hub.events.collect {
                if (it.kind == "answer" || it.kind == "direct_reply") { delay(400); refreshTurns() }  // turn is logged just after
            }
        }
    }

    // ----- talking -----
    fun toggleMic() {
        when (phase.value) {
            Phase.LISTENING -> finishRecording()
            Phase.SPEAKING -> { player.stop(); phase.value = Phase.IDLE }
            Phase.THINKING -> Unit
            Phase.IDLE -> startRecording()
        }
    }

    private fun startRecording() {
        api() ?: return
        error.value = ""
        player.stop()
        recorder = Recorder(onAutoStop = { viewModelScope.launch { finishRecording() } }).also {
            runCatching { it.start() }.onFailure { e -> error.value = "Mic unavailable: ${e.message}"; recorder = null; return }
        }
        phase.value = Phase.LISTENING
    }

    private fun finishRecording() {
        val rec = recorder ?: return
        recorder = null
        viewModelScope.launch {
            val wav = withContext(Dispatchers.IO) { rec.stop() }
            if (wav.isEmpty()) { phase.value = Phase.IDLE; return@launch }
            phase.value = Phase.THINKING
            heard.value = "…"
            reply.value = ""
            val api = api() ?: run { phase.value = Phase.IDLE; return@launch }
            runCatching { api.voice(wav) }
                .onSuccess { (h, r) ->
                    heard.value = h.ifBlank { "(didn't catch that)" }
                    reply.value = r
                    if (h.isNotBlank()) refreshTurns()
                    speakOrIdle(api, r)
                }
                .onFailure { fail(it) }
        }
    }

    fun send(text: String) {
        val t = text.trim()
        if (t.isEmpty() || phase.value == Phase.THINKING) return
        val api = api() ?: return
        error.value = ""
        heard.value = t
        reply.value = ""
        phase.value = Phase.THINKING
        turns.value = turns.value + Turn(t, "", "", pending = true)
        viewModelScope.launch {
            runCatching { api.command(t) }
                .onSuccess { r -> reply.value = r; refreshTurns(); speakOrIdle(api, r) }
                .onFailure { fail(it) }
        }
    }

    private suspend fun speakOrIdle(api: MaxApi, text: String) {
        if (text.isBlank() || !prefs.speakReplies) { phase.value = Phase.IDLE; return }
        val wav = runCatching { api.speak(text) }.getOrNull()
        if (wav == null) { phase.value = Phase.IDLE; return }
        phase.value = Phase.SPEAKING
        runCatching { player.play(wav) { phase.value = Phase.IDLE } }.onFailure { phase.value = Phase.IDLE }
    }

    private fun fail(e: Throwable) {
        phase.value = Phase.IDLE
        error.value = when {
            e is ApiError && e.code == 401 -> "The laptop rejected this phone. Pair again in Settings."
            e is ApiError -> e.message ?: "Error ${e.code}"
            else -> "Can't reach the laptop. Is it on, with Max and Tailscale running?"
        }
    }

    // ----- conversation -----
    fun refreshTurns() {
        val api = MaxApi.from(prefs) ?: return
        viewModelScope.launch {
            runCatching { api.turns() }.onSuccess { arr ->
                turns.value = arr.objects().map { Turn(it.optString("user"), it.optString("reply"), it.optString("ts")) }
            }
        }
    }

    // ----- approvals -----
    fun answer(id: Int, approved: Boolean) {
        val api = MaxApi.from(prefs) ?: return
        Hub.removeApproval(id)
        viewModelScope.launch { runCatching { api.approve(id, approved) } }
    }

    // ----- pairing -----
    fun pair(link: String): Boolean {
        if (!prefs.pairFrom(link)) return false
        onPaired()
        return true
    }

    fun pairManually(url: String, token: String) {
        prefs.url = if (url.startsWith("http")) url else "https://$url"
        prefs.token = token
        onPaired()
    }

    private fun onPaired() {
        paired.value = prefs.paired
        error.value = ""
        val ctx = getApplication<Application>()
        ctx.startForegroundService(android.content.Intent(ctx, MaxService::class.java).setAction(MaxService.ACTION_RECONNECT))
        refreshTurns()
    }

    fun unpair() {
        val ctx = getApplication<Application>()
        ctx.stopService(android.content.Intent(ctx, MaxService::class.java))
        prefs.clear()
        paired.value = false
        turns.value = emptyList()
    }

    /** Runs an API call and reports failures on the shared error line. */
    fun <T> call(block: suspend (MaxApi) -> T, onResult: (T) -> Unit = {}) {
        val api = api() ?: return
        viewModelScope.launch {
            runCatching { block(api) }.onSuccess(onResult).onFailure { fail(it) }
        }
    }

    override fun onCleared() {
        recorder?.stop()
        player.stop()
    }
}

fun JSONObject.str(key: String) = if (isNull(key)) "" else optString(key)

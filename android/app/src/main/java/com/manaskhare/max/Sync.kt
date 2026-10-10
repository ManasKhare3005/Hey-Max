package com.manaskhare.max

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * Brings the phone and the laptop together whenever the laptop can be reached (the link comes
 * up, the app opens, something changes here, the laptop says something changed there):
 *   1. edits made in the calendar app are read back (MaxCalendar.readBack)
 *   2. reminders and events changed on the phone go to POST /api/sync; the laptop's merged list
 *      comes back and replaces what the phone knows (SyncRules.plan)
 *   3. alarms are re-armed and the Max calendar is made to match
 *   4. the Today page (Canvas due dates, classes) is cached for offline use
 *   5. recordings made on the phone are uploaded, and finished ones checked for their notes
 * Nothing is lost if the laptop can't be reached: changes stay marked and go next time.
 */
object Sync {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mutex = Mutex()
    private var pending: Job? = null
    val running = MutableStateFlow(false)
    val lastSync = MutableStateFlow(0L)

    /** Sync soon (several triggers close together make one sync). */
    fun trigger(context: Context, delayMs: Long = 700) {
        val app = context.applicationContext
        pending?.cancel()
        pending = scope.launch { delay(delayMs); run(app) }
    }

    /** One sync now; false if the laptop couldn't be reached (or this phone isn't paired). */
    suspend fun run(context: Context): Boolean = mutex.withLock {
        val prefs = Prefs(context)
        if (lastSync.value == 0L) lastSync.value = prefs.lastSync
        val api = MaxApi.from(prefs) ?: return false
        val db = LocalDb.get(context)
        running.value = true
        try {
            runCatching { MaxCalendar.readBack(context) }
            val rems = db.reminders("dirty = 1")
            val evs = db.events("dirty = 1")
            val body = JSONObject()
                .put("reminders", JSONArray(rems.map { it.toJson() }))
                .put("events", JSONArray(evs.map { it.toJson() }))
            val res = runCatching { api.sync(body) }.getOrElse { return false }

            val serverRems = res.optJSONArray("reminders")?.objects().orEmpty().map { LocalReminder.fromJson(it) }
            val rPlan = SyncRules.plan(db.reminders(), serverRems, rems.associate { it.uid to it.updatedMs })
            rPlan.write.forEach { db.put(it) }
            rPlan.delete.forEach { db.delete("reminders", it); Alarms.cancel(context, it) }

            val serverEvs = res.optJSONArray("events")?.objects().orEmpty().map { LocalEvent.fromJson(it) }
            val ePlan = SyncRules.plan(db.events(), serverEvs, evs.associate { it.uid to it.updatedMs })
            ePlan.write.forEach { db.put(it) }
            ePlan.delete.forEach { db.delete("events", it) }

            Alarms.armAll(context)
            runCatching { MaxCalendar.mirror(context) }
            runCatching { api.today() }.onSuccess { prefs.todayJson = it.toString(); prefs.todayAt = System.currentTimeMillis() }
            recordings(context, api, db)
            prefs.lastSync = System.currentTimeMillis()
            lastSync.value = prefs.lastSync
            true
        } finally {
            running.value = false
        }
    }

    /** Upload waiting recordings; ask about ones the laptop is still writing up. */
    private suspend fun recordings(context: Context, api: MaxApi, db: LocalDb) {
        for (r in db.recordings("state IN ('waiting', 'uploading')")) {
            val file = File(r.file)
            if (!file.exists() || file.length() == 0L) {
                db.put(r.copy(state = "failed", error = "The recording file is missing."))
                continue
            }
            db.put(r.copy(state = "uploading", error = ""))
            runCatching { api.uploadRecording(r, file) }
                .onSuccess { applyStatus(context, db, db.recording(r.uid) ?: r, it) }
                .onFailure {
                    db.put(r.copy(state = "waiting", error = if (it is ApiError) it.message ?: "" else ""))
                    return                                          // the laptop went away: try again next sync
                }
        }
        for (r in db.recordings("state = 'processing'")) {
            runCatching { api.recordingStatus(r.uid) }
                .onSuccess { applyStatus(context, db, r, it) }
                .onFailure { if (it is ApiError && it.code == 404) db.put(r.copy(state = "waiting")) }   // laptop lost it: send again
        }
    }

    /** The laptop's word on a recording: queued / processing / done (note_id) / failed. */
    fun applyStatus(context: Context, db: LocalDb, r: LocalRecording, st: JSONObject) {
        when (st.optString("state")) {
            "done" -> finished(context, db, r, st.optInt("note_id"))
            "failed" -> db.put(r.copy(state = "failed", error = st.optString("error").ifBlank { "The laptop couldn't write notes." }))
            else -> db.put(r.copy(state = "processing", error = ""))
        }
    }

    /** Notes are saved on the laptop: drop the phone's copy of the audio, say so once. */
    fun finished(context: Context, db: LocalDb, r: LocalRecording, noteId: Int) {
        if (r.state == "done") return
        runCatching { File(r.file).delete() }
        db.put(r.copy(state = "done", noteId = noteId, error = ""))
        val nm = context.getSystemService(NotificationManager::class.java)
        val open = PendingIntent.getActivity(context, 0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        nm.notify(30_000 + (r.uid.hashCode() and 0xffff), NotificationCompat.Builder(context, MaxApp.CH_UPDATE)
            .setSmallIcon(R.drawable.ic_stat_max)
            .setContentTitle("Notes ready")
            .setContentText(r.title + " · recorded on your phone")
            .setAutoCancel(true)
            .setContentIntent(open)
            .build())
    }
}

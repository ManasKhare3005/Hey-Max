package com.manaskhare.max

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONObject
import java.util.UUID

/**
 * What the phone keeps on its own, so Max's reminders, calendar and recordings work with the
 * laptop off: a small SQLite database. Changes are saved here first (marked `dirty`) and sent to
 * the laptop by Sync; the laptop's answer then replaces what it knows better (see SyncRules).
 */
data class LocalReminder(
    override val uid: String, val text: String, val dueMs: Long, val status: String = "pending",
    override val updatedMs: Long = System.currentTimeMillis(), override val dirty: Boolean = true,
    override val synced: Boolean = false,
) : Syncable {
    override val finished get() = status != "pending"
    fun toJson(): JSONObject = JSONObject().put("uid", uid).put("text", text).put("due_ms", dueMs)
        .put("status", status).put("updated_ms", updatedMs)

    companion object {
        fun fromJson(o: JSONObject) = LocalReminder(o.optString("uid"), o.optString("text"), o.optLong("due_ms"),
            o.optString("status", "pending"), o.optLong("updated_ms"), dirty = false, synced = true)
    }
}

data class LocalEvent(
    override val uid: String, val title: String, val startMs: Long, val endMs: Long, val allDay: Boolean = false,
    val location: String = "", val status: String = "active",
    override val updatedMs: Long = System.currentTimeMillis(), override val dirty: Boolean = true,
    override val synced: Boolean = false,
) : Syncable {
    override val finished get() = status == "cancelled"
    fun toJson(): JSONObject = JSONObject().put("uid", uid).put("title", title).put("start_ms", startMs)
        .put("end_ms", endMs).put("all_day", allDay).put("location", location).put("status", status)
        .put("updated_ms", updatedMs)

    companion object {
        fun fromJson(o: JSONObject) = LocalEvent(o.optString("uid"), o.optString("title"), o.optLong("start_ms"),
            o.optLong("end_ms"), o.optBoolean("all_day"), o.optString("location"), o.optString("status", "active"),
            o.optLong("updated_ms"), dirty = false, synced = true)
    }
}

/** A lecture / meeting recorded on the phone. state: recording, waiting (for the laptop), uploading,
 *  processing (the laptop is writing the notes), done (note_id), failed. */
data class LocalRecording(
    val uid: String, val kind: String, val title: String, val startedMs: Long, val durationS: Long,
    val file: String, val state: String, val noteId: Int = 0, val error: String = "",
)

class LocalDb private constructor(context: Context) : SQLiteOpenHelper(context, "max-local.db", null, 1) {
    override fun onCreate(db: SQLiteDatabase) {
        db.execSQL("CREATE TABLE reminders (uid TEXT PRIMARY KEY, text TEXT NOT NULL, due_ms INTEGER NOT NULL, " +
                   "status TEXT NOT NULL, updated_ms INTEGER NOT NULL, dirty INTEGER NOT NULL, synced INTEGER NOT NULL)")
        db.execSQL("CREATE TABLE events (uid TEXT PRIMARY KEY, title TEXT NOT NULL, start_ms INTEGER NOT NULL, " +
                   "end_ms INTEGER NOT NULL, all_day INTEGER NOT NULL, location TEXT NOT NULL, status TEXT NOT NULL, " +
                   "updated_ms INTEGER NOT NULL, dirty INTEGER NOT NULL, synced INTEGER NOT NULL)")
        db.execSQL("CREATE TABLE recordings (uid TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, " +
                   "started_ms INTEGER NOT NULL, duration_s INTEGER NOT NULL, file TEXT NOT NULL, state TEXT NOT NULL, " +
                   "note_id INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '')")
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {}

    // ----- reminders -----
    fun reminders(where: String = "1", vararg args: String): List<LocalReminder> =
        readableDatabase.rawQuery("SELECT * FROM reminders WHERE $where ORDER BY due_ms", args).use { c ->
            generateSequence { if (c.moveToNext()) c else null }.map {
                LocalReminder(c.s("uid"), c.s("text"), c.l("due_ms"), c.s("status"), c.l("updated_ms"),
                              c.l("dirty") == 1L, c.l("synced") == 1L)
            }.toList()
        }

    fun reminder(uid: String) = reminders("uid = ?", uid).firstOrNull()

    fun put(r: LocalReminder) {
        writableDatabase.insertWithOnConflict("reminders", null, ContentValues().apply {
            put("uid", r.uid); put("text", r.text); put("due_ms", r.dueMs); put("status", r.status)
            put("updated_ms", r.updatedMs); put("dirty", if (r.dirty) 1 else 0); put("synced", if (r.synced) 1 else 0)
        }, SQLiteDatabase.CONFLICT_REPLACE)
        changed()
    }

    // ----- events -----
    fun events(where: String = "1", vararg args: String): List<LocalEvent> =
        readableDatabase.rawQuery("SELECT * FROM events WHERE $where ORDER BY start_ms", args).use { c ->
            generateSequence { if (c.moveToNext()) c else null }.map {
                LocalEvent(c.s("uid"), c.s("title"), c.l("start_ms"), c.l("end_ms"), c.l("all_day") == 1L,
                           c.s("location"), c.s("status"), c.l("updated_ms"), c.l("dirty") == 1L, c.l("synced") == 1L)
            }.toList()
        }

    fun event(uid: String) = events("uid = ?", uid).firstOrNull()

    fun put(e: LocalEvent) {
        writableDatabase.insertWithOnConflict("events", null, ContentValues().apply {
            put("uid", e.uid); put("title", e.title); put("start_ms", e.startMs); put("end_ms", e.endMs)
            put("all_day", if (e.allDay) 1 else 0); put("location", e.location); put("status", e.status)
            put("updated_ms", e.updatedMs); put("dirty", if (e.dirty) 1 else 0); put("synced", if (e.synced) 1 else 0)
        }, SQLiteDatabase.CONFLICT_REPLACE)
        changed()
    }

    fun delete(table: String, uid: String) {
        require(table in setOf("reminders", "events", "recordings"))
        writableDatabase.delete(table, "uid = ?", arrayOf(uid))
        changed()
    }

    // ----- recordings -----
    fun recordings(where: String = "1", vararg args: String): List<LocalRecording> =
        readableDatabase.rawQuery("SELECT * FROM recordings WHERE $where ORDER BY started_ms DESC", args).use { c ->
            generateSequence { if (c.moveToNext()) c else null }.map {
                LocalRecording(c.s("uid"), c.s("kind"), c.s("title"), c.l("started_ms"), c.l("duration_s"),
                               c.s("file"), c.s("state"), c.l("note_id").toInt(), c.s("error"))
            }.toList()
        }

    fun recording(uid: String) = recordings("uid = ?", uid).firstOrNull()

    fun put(r: LocalRecording) {
        writableDatabase.insertWithOnConflict("recordings", null, ContentValues().apply {
            put("uid", r.uid); put("kind", r.kind); put("title", r.title); put("started_ms", r.startedMs)
            put("duration_s", r.durationS); put("file", r.file); put("state", r.state); put("note_id", r.noteId)
            put("error", r.error)
        }, SQLiteDatabase.CONFLICT_REPLACE)
        changed()
    }

    private fun Cursor.s(col: String): String = getString(getColumnIndexOrThrow(col)) ?: ""
    private fun Cursor.l(col: String): Long = getLong(getColumnIndexOrThrow(col))

    companion object {
        @Volatile private var instance: LocalDb? = null
        fun get(context: Context): LocalDb =
            instance ?: synchronized(this) { instance ?: LocalDb(context.applicationContext).also { instance = it } }

        /** Bumped on every change, so screens showing local data refresh. */
        val revision = MutableStateFlow(0)
        fun changed() { revision.value++ }

        fun newUid(): String = UUID.randomUUID().toString().replace("-", "")
    }
}

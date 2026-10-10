package com.manaskhare.max

import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.time.LocalDateTime
import java.time.ZoneId

/**
 * Reminders and events made or removed on the phone: saved here right away (alarm armed, Max
 * calendar updated), then sent to the laptop by Sync whenever it can be reached.
 */
object Offline {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    fun ms(t: LocalDateTime): Long = t.atZone(ZoneId.systemDefault()).toInstant().toEpochMilli()

    private fun after(context: Context, calendar: Boolean) {
        val app = context.applicationContext
        scope.launch {
            runCatching { Alarms.armAll(app) }
            if (calendar) runCatching { MaxCalendar.mirror(app) }
        }
        Sync.trigger(app)
    }

    fun addReminder(context: Context, text: String, at: LocalDateTime): LocalReminder {
        val r = LocalReminder(LocalDb.newUid(), text.trim(), ms(at))
        LocalDb.get(context).put(r)
        after(context, calendar = false)
        return r
    }

    fun cancelReminder(context: Context, uid: String) {
        val db = LocalDb.get(context)
        val r = db.reminder(uid) ?: return
        db.put(r.copy(status = "cancelled", updatedMs = System.currentTimeMillis(), dirty = true))
        Alarms.cancel(context, uid)
        after(context, calendar = false)
    }

    /** An event; no time of day = all day. */
    fun addEvent(context: Context, title: String, w: When, minutes: Int = 60, location: String = ""): LocalEvent {
        val start = if (w.hasTime) w.at else w.at.toLocalDate().atStartOfDay()
        val end = if (w.hasTime) start.plusMinutes(minutes.toLong()) else start.plusDays(1)
        val e = LocalEvent(LocalDb.newUid(), title.trim(), ms(start), ms(end), allDay = !w.hasTime, location = location.trim())
        LocalDb.get(context).put(e)
        after(context, calendar = true)
        return e
    }

    fun cancelEvent(context: Context, uid: String) {
        val db = LocalDb.get(context)
        val e = db.event(uid) ?: return
        db.put(e.copy(status = "cancelled", updatedMs = System.currentTimeMillis(), dirty = true))
        after(context, calendar = true)
    }
}

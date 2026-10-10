package com.manaskhare.max.ui

import android.app.DatePickerDialog
import android.app.TimePickerDialog
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manaskhare.max.Hub
import com.manaskhare.max.Link
import com.manaskhare.max.Sync
import com.manaskhare.max.When
import com.manaskhare.max.WhenParser
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

private val DAY = DateTimeFormatter.ofPattern("EEE MMM d", Locale.US)
private val CLOCK = DateTimeFormatter.ofPattern("h:mm a", Locale.US)

/** "Today · 5:00 PM", "Tomorrow", "Fri Oct 10 · 9:00 AM" */
fun whenLabel(at: LocalDateTime, hasTime: Boolean = true, today: LocalDate = LocalDate.now()): String {
    val d = at.toLocalDate()
    val day = when (d) {
        today -> "Today"
        today.plusDays(1) -> "Tomorrow"
        else -> DAY.format(at)
    }
    return if (hasTime) "$day · ${CLOCK.format(at)}" else day
}

fun whenLabel(ms: Long, hasTime: Boolean = true) =
    whenLabel(Instant.ofEpochMilli(ms).atZone(ZoneId.systemDefault()).toLocalDateTime(), hasTime)

/** "3 min ago", "2 h ago", "yesterday" */
fun ago(ms: Long): String {
    if (ms <= 0) return "never"
    val s = (System.currentTimeMillis() - ms) / 1000
    return when {
        s < 90 -> "just now"
        s < 3600 -> "${s / 60} min ago"
        s < 86_400 -> "${s / 3600} h ago"
        s < 172_800 -> "yesterday"
        else -> "${s / 86_400} days ago"
    }
}

/**
 * A "when?" box: typed ("tomorrow 5pm", "fri 9am", "in 2 hours") with what it understood shown
 * underneath, or picked with the calendar button.
 */
@Composable
fun WhenField(value: String, onChange: (String) -> Unit, hint: String, allDayOk: Boolean = false): When? {
    val context = LocalContext.current
    val parsed = if (value.isBlank()) null else WhenParser.parse(value)
    Row(verticalAlignment = Alignment.CenterVertically) {
        Field(value, onChange, hint, Modifier.weight(1f))
        IconButton(onClick = {
            val now = LocalDateTime.now()
            DatePickerDialog(context, { _, y, m, d ->
                TimePickerDialog(context, { _, h, min ->
                    onChange(String.format(Locale.US, "%04d-%02d-%02d %02d:%02d", y, m + 1, d, h, min))
                }, 9, 0, false).apply {
                    if (allDayOk) setButton(android.content.DialogInterface.BUTTON_NEUTRAL, "All day") { _, _ ->
                        onChange(String.format(Locale.US, "%04d-%02d-%02d", y, m + 1, d))
                    }
                }.show()
            }, now.year, now.monthValue - 1, now.dayOfMonth).show()
        }) { Icon(Icons.Filled.CalendarMonth, contentDescription = "Pick a date", tint = Cyan) }
    }
    if (value.isNotBlank()) {
        Text(if (parsed == null) "Didn't get that: try “tomorrow 5pm” or use 📅"
             else "→ " + whenLabel(parsed.at, parsed.hasTime) + if (!parsed.hasTime && allDayOk) " (all day)" else "",
             color = if (parsed == null) Amber else Green, fontSize = 12.5.sp, modifier = Modifier.padding(start = 4.dp, top = 2.dp))
    }
    return parsed
}

/** "Offline · synced 2 h ago" when the laptop can't be reached. */
@Composable
fun OfflineNote(modifier: Modifier = Modifier) {
    val link by Hub.link.collectAsState()
    val last by Sync.lastSync.collectAsState()
    if (link == Link.ONLINE) return
    Column(modifier) {
        Label(if (last > 0) "laptop offline · synced ${ago(last)}" else "laptop offline · not synced yet", color = Amber)
        Text("Reminders and events you add here are kept on this phone and sent to the laptop later.",
             color = Text2, fontSize = 12.5.sp)
    }
}

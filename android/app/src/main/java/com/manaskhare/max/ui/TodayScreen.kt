package com.manaskhare.max.ui

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manaskhare.max.Hub
import com.manaskhare.max.Link
import com.manaskhare.max.LocalDb
import com.manaskhare.max.MainViewModel
import com.manaskhare.max.Offline
import com.manaskhare.max.Sync
import com.manaskhare.max.objects
import com.manaskhare.max.str
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * What's due (Canvas), today's classes, your calendar and your reminders. Calendar and reminders
 * live on the phone too, so they work (and can be added) with the laptop off; Canvas shows the
 * copy from the last sync when the laptop can't be reached.
 */
@Composable
fun TodayScreen(vm: MainViewModel) {
    val context = LocalContext.current
    val db = remember { LocalDb.get(context) }
    val rev by LocalDb.revision.collectAsState()
    val link by Hub.link.collectAsState()
    var today by remember { mutableStateOf(vm.prefs.todayJson.takeIf { it.isNotBlank() }?.let { runCatching { JSONObject(it) }.getOrNull() }) }
    var refresh by remember { mutableStateOf(0) }
    LaunchedEffect(refresh, link) {
        if (link == Link.ONLINE) vm.quiet({ it.today() }) { today = it; vm.prefs.todayJson = it.toString(); vm.prefs.todayAt = System.currentTimeMillis() }
    }
    val now = System.currentTimeMillis()
    val reminders by produceList(rev) { db.reminders("status = 'pending'") }
    val events by produceList(rev) { db.events("status = 'active' AND end_ms > ? AND start_ms < ?", now.toString(), (now + 14 * 86_400_000L).toString()) }

    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Row(Modifier.fillMaxWidth().padding(start = 10.dp, top = 22.dp), verticalAlignment = Alignment.Bottom) {
                Column(Modifier.weight(1f)) {
                    Label(java.time.LocalDate.now().format(java.time.format.DateTimeFormatter.ofPattern("EEEE · MMMM d")), color = Accent0)
                    Text("Today", style = DisplayStyle, color = Text1, fontSize = 48.sp, lineHeight = 52.sp)
                }
                GhostButton("Refresh") { refresh++; Sync.trigger(context, 0) }
            }
            OfflineNote(Modifier.padding(start = 10.dp, top = 6.dp))
        }
        val t = today
        if (t == null) {
            item { Text(if (link == Link.ONLINE) "Loading…" else "Canvas due dates and classes appear after the first sync.", color = Text2) }
        } else {
            if (t.str("canvas_error").isNotBlank()) item { Text("Canvas: ${t.str("canvas_error")}", color = Amber, fontSize = 13.sp) }
            item { DueSection("Due today", t.optJSONArray("due_today")?.objects().orEmpty(), "Nothing due today", highlight = true) }
            item { DueSection("Due soon", t.optJSONArray("due_soon")?.objects().orEmpty(), "Nothing in the next few days") }
            item { DueSection("Classes", t.optJSONArray("classes")?.objects().orEmpty(), "No classes today") }
        }
        item { CalendarCard(events) }
        item { RemindersCard(reminders) }
        item { Spacer(Modifier.height(16.dp)) }
    }
}

/** Reads from the local database off the main thread whenever `key` changes. */
@Composable
private fun <T> produceList(key: Any, read: () -> List<T>) =
    androidx.compose.runtime.produceState(initialValue = emptyList<T>(), key) { value = withContext(Dispatchers.IO) { read() } }

@Composable
internal fun CalendarCard(events: List<com.manaskhare.max.LocalEvent>) {
    val context = LocalContext.current
    var title by remember { mutableStateOf("") }
    var whenText by remember { mutableStateOf("") }
    var added by remember { mutableStateOf("") }
    Card {
        Label("Calendar")
        if (events.isEmpty()) Text("Nothing in the next two weeks", color = Text2, modifier = Modifier.padding(top = 6.dp))
        events.forEach { e ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f).padding(vertical = 6.dp)) {
                    Text(e.title, color = Text1)
                    Label(whenLabel(e.startMs, !e.allDay) + (if (e.allDay) " · all day" else "") +
                          (if (e.location.isNotBlank()) " · ${e.location}" else "") + (if (e.dirty) " · not synced yet" else ""))
                }
                IconButton(onClick = { Offline.cancelEvent(context, e.uid) }) {
                    Icon(Icons.Filled.Close, contentDescription = "Remove event", tint = Dim)
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        Field(title, { title = it }, "Add an event…")
        Spacer(Modifier.height(6.dp))
        val parsed = WhenField(whenText, { whenText = it }, "When? (tomorrow 3pm, fri, oct 12)", allDayOk = true)
        Spacer(Modifier.height(8.dp))
        GhostButton("Add event") {
            if (title.isNotBlank() && parsed != null) {
                val e = Offline.addEvent(context, title, parsed)
                added = "Added: ${e.title}, ${whenLabel(e.startMs, !e.allDay)}"
                title = ""; whenText = ""
            }
        }
        if (added.isNotBlank()) Text(added, color = Green, fontSize = 13.sp)
        Text("Shows in your phone's calendar app as “Max” (Settings → Calendar).", color = Text2, fontSize = 12.sp,
             modifier = Modifier.padding(top = 6.dp))
    }
}

@Composable
internal fun RemindersCard(reminders: List<com.manaskhare.max.LocalReminder>) {
    val context = LocalContext.current
    var what by remember { mutableStateOf("") }
    var whenText by remember { mutableStateOf("") }
    var added by remember { mutableStateOf("") }
    Card {
        Label("Reminders")
        if (reminders.isEmpty()) Text("None set", color = Text2, modifier = Modifier.padding(top = 6.dp))
        reminders.forEach { r ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f).padding(vertical = 6.dp)) {
                    Text(r.text, color = Text1)
                    Label(whenLabel(r.dueMs) + if (r.dirty) " · not synced yet" else "")
                }
                IconButton(onClick = { Offline.cancelReminder(context, r.uid) }) {
                    Icon(Icons.Filled.Close, contentDescription = "Cancel reminder", tint = Dim)
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        Field(what, { what = it }, "Remind me to…")
        Spacer(Modifier.height(6.dp))
        val parsed = WhenField(whenText, { whenText = it }, "When? (tomorrow 9am, in 2 hours)")
        Spacer(Modifier.height(8.dp))
        GhostButton("Add reminder") {
            if (what.isNotBlank() && parsed != null) {
                val r = Offline.addReminder(context, what, parsed.at)
                added = "Set for ${whenLabel(r.dueMs)}"
                what = ""; whenText = ""
            }
        }
        if (added.isNotBlank()) Text(added, color = Green, fontSize = 13.sp)
    }
}

@Composable
private fun DueSection(title: String, items: List<JSONObject>, empty: String, highlight: Boolean = false) {
    val hot = highlight && items.isNotEmpty()
    Card(if (hot) Modifier.border(1.dp, Accent0.copy(alpha = 0.45f), RoundedCornerShape(20.dp)) else Modifier) {
        Label(title, color = if (hot) Accent0 else Text2)
        if (items.isEmpty()) Text(empty, color = Text2, modifier = Modifier.padding(top = 6.dp))
        items.forEach { i ->
            Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(i.str("title"), style = if (hot) DisplayStyle else TextStyle.Default, color = Text1,
                         fontSize = if (hot) 24.sp else 16.sp, fontWeight = if (hot) null else FontWeight.SemiBold)
                    if (i.str("course").isNotBlank()) Text(i.str("course"), color = Text2, fontSize = 13.sp)
                }
                if (i.str("when").isNotBlank()) Text(i.str("when"), style = DisplayStyle, color = Accent0, fontSize = 18.sp,
                                                     fontStyle = FontStyle.Italic, modifier = Modifier.padding(start = 12.dp))
            }
        }
    }
}

@Composable
fun Field(value: String, onChange: (String) -> Unit, hint: String, modifier: Modifier = Modifier.fillMaxWidth()) =
    OutlinedTextField(
        value = value, onValueChange = onChange, modifier = modifier, singleLine = true,
        placeholder = { Text(hint, color = Dim, fontSize = 14.sp) }, shape = RoundedCornerShape(10.dp),
        colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Line, focusedBorderColor = Cyan),
    )

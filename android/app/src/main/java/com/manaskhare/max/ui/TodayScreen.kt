package com.manaskhare.max.ui

import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manaskhare.max.MainViewModel
import com.manaskhare.max.objects
import com.manaskhare.max.str
import org.json.JSONObject

/** What's due (Canvas), today's classes and your reminders, with quick add/cancel. */
@Composable
fun TodayScreen(vm: MainViewModel) {
    var today by remember { mutableStateOf<JSONObject?>(null) }
    var rev by remember { mutableStateOf(0) }
    var what by remember { mutableStateOf("") }
    var whenText by remember { mutableStateOf("") }
    var added by remember { mutableStateOf("") }
    LaunchedEffect(rev) { vm.call({ it.today() }) { today = it } }

    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Row(Modifier.fillMaxWidth().padding(start = 10.dp, top = 22.dp), verticalAlignment = Alignment.Bottom) {
                Column(Modifier.weight(1f)) {
                    Label(java.time.LocalDate.now().format(java.time.format.DateTimeFormatter.ofPattern("EEEE · MMMM d")), color = Accent0)
                    Text("Today", style = DisplayStyle, color = Text1, fontSize = 48.sp, lineHeight = 52.sp)
                }
                GhostButton("Refresh") { rev++ }
            }
        }
        val t = today
        if (t == null) {
            item { Text("Loading…", color = Text2) }
        } else {
            if (t.str("canvas_error").isNotBlank()) item { Text("Canvas: ${t.str("canvas_error")}", color = Amber, fontSize = 13.sp) }
            item { DueSection("Due today", t.optJSONArray("due_today")?.objects().orEmpty(), "Nothing due today", highlight = true) }
            item { DueSection("Due soon", t.optJSONArray("due_soon")?.objects().orEmpty(), "Nothing in the next few days") }
            item { DueSection("Classes", t.optJSONArray("classes")?.objects().orEmpty(), "No classes today") }
            item {
                Card {
                    Label("Reminders")
                    val rs = t.optJSONArray("reminders")?.objects().orEmpty()
                    if (rs.isEmpty()) Text("None set", color = Text2, modifier = Modifier.padding(top = 6.dp))
                    rs.forEach { r ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f).padding(vertical = 6.dp)) {
                                Text(r.str("text"), color = Text1)
                                Label(r.str("due").replace("T", " ").take(16))
                            }
                            IconButton(onClick = { vm.call({ it.cancelReminder(r.optInt("id")) }) { rev++ } }) {
                                Icon(Icons.Filled.Close, contentDescription = "Cancel reminder", tint = Dim)
                            }
                        }
                    }
                    Spacer(Modifier.height(10.dp))
                    Field(what, { what = it }, "Remind me to…")
                    Spacer(Modifier.height(6.dp))
                    Field(whenText, { whenText = it }, "When? (e.g. tomorrow 9am, friday 5pm)")
                    Spacer(Modifier.height(8.dp))
                    GhostButton("Add reminder") {
                        if (what.isNotBlank() && whenText.isNotBlank()) {
                            vm.call({ it.addReminder(what, whenText) }) { res ->
                                added = "Set for ${res.optString("spoken")}"
                                what = ""; whenText = ""; rev++
                            }
                        }
                    }
                    if (added.isNotBlank()) Text(added, color = Green, fontSize = 13.sp)
                }
            }
            item { Spacer(Modifier.height(16.dp)) }
        }
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

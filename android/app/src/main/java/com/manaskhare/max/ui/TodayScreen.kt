package com.manaskhare.max.ui

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
            Row(Modifier.fillMaxWidth().padding(top = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                Title("Today")
                Spacer(Modifier.weight(1f))
                GhostButton("Refresh") { rev++ }
            }
        }
        val t = today
        if (t == null) {
            item { Text("Loading…", color = Text2) }
        } else {
            if (t.str("canvas_error").isNotBlank()) item { Text("Canvas: ${t.str("canvas_error")}", color = Amber, fontSize = 13.sp) }
            item { Section("Due today", t.optJSONArray("due_today")?.objects().orEmpty(), "Nothing due today 🎉") }
            item { Section("Due soon", t.optJSONArray("due_soon")?.objects().orEmpty(), "Nothing in the next few days") }
            item { Section("Classes", t.optJSONArray("classes")?.objects().orEmpty(), "No classes today") }
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
private fun Section(title: String, items: List<JSONObject>, empty: String) {
    Card {
        Label(title)
        if (items.isEmpty()) Text(empty, color = Text2, modifier = Modifier.padding(top = 6.dp))
        items.forEach { i ->
            Column(Modifier.padding(vertical = 6.dp)) {
                Text(i.str("title"), color = Text1, fontSize = 15.sp)
                Label(listOf(i.str("course"), i.str("when")).filter { it.isNotBlank() }.joinToString(" · "))
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

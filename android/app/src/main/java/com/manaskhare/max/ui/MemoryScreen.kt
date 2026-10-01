package com.manaskhare.max.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manaskhare.max.MainViewModel
import com.manaskhare.max.objects
import com.manaskhare.max.str
import kotlinx.coroutines.delay
import org.json.JSONObject

/** Facts Max remembers, plus meeting & lecture notes (record from here too). */
@Composable
fun MemoryScreen(vm: MainViewModel) {
    var tab by remember { mutableIntStateOf(0) }
    var openNote by remember { mutableStateOf<JSONObject?>(null) }

    openNote?.let { NoteDetail(vm, it) { openNote = null }; return }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(Modifier.padding(start = 10.dp, top = 22.dp)) { Title("Memory") }
        Spacer(Modifier.height(10.dp))
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            listOf("Facts", "Notes").forEachIndexed { i, label ->
                SegmentedButton(selected = tab == i, onClick = { tab = i },
                                shape = SegmentedButtonDefaults.itemShape(i, 2)) { Text(label) }
            }
        }
        Spacer(Modifier.height(12.dp))
        if (tab == 0) Facts(vm) else Notes(vm) { openNote = it }
    }
}

@Composable
private fun Facts(vm: MainViewModel) {
    var facts by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var rev by remember { mutableIntStateOf(0) }
    var text by remember { mutableStateOf("") }
    LaunchedEffect(rev) { vm.call({ it.facts() }) { facts = it.objects() } }

    Row(verticalAlignment = Alignment.CenterVertically) {
        Field(text, { text = it }, "Teach Max a fact…", Modifier.weight(1f))
        Spacer(Modifier.width(8.dp))
        GhostButton("Add") { if (text.isNotBlank()) vm.call({ it.addFact(text) }) { text = ""; rev++ } }
    }
    Spacer(Modifier.height(10.dp))
    if (facts.isEmpty()) Text("Max doesn't remember anything yet. Say “remember that…”.", color = Text2)
    LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        items(facts, key = { it.optInt("id") }) { f ->
            Card {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(f.str("text"), color = Text1)
                        Label(f.str("created").replace("T", " ").take(16))
                    }
                    IconButton(onClick = { vm.call({ it.deleteFact(f.optInt("id")) }) { rev++ } }) {
                        Icon(Icons.Filled.Delete, contentDescription = "Forget", tint = Dim)
                    }
                }
            }
        }
    }
}

@Composable
private fun Notes(vm: MainViewModel, open: (JSONObject) -> Unit) {
    var items by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var status by remember { mutableStateOf<JSONObject?>(null) }
    var rev by remember { mutableIntStateOf(0) }
    LaunchedEffect(rev) { vm.call({ it.notes() }) { items = it.objects() } }
    LaunchedEffect(Unit) {
        while (true) {                    // live recording status
            vm.call({ it.notesStatus() }) { status = it }
            delay(1000)
        }
    }

    val s = status
    Card {
        when {
            s?.optBoolean("active") == true -> {
                Label("● recording ${s.str("kind")}", color = Red)
                Text("${s.optInt("elapsed_s") / 60} min · ${s.optInt("words")} words", color = Text1)
                if (s.str("last").isNotBlank()) Text("“…${s.str("last")}”", color = Text2, fontSize = 13.sp)
                Spacer(Modifier.height(8.dp))
                GhostButton("Stop & write notes", color = Red) { vm.call({ it.notesStop() }) { rev++ } }
            }
            s?.optBoolean("finishing") == true -> Text("✍ Writing up the notes…", color = Text1)
            else -> {
                Label("record on the laptop")
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    GhostButton("● Lecture", Modifier.weight(1f)) { vm.call({ it.notesStart("lecture") }) }
                    GhostButton("● Meeting", Modifier.weight(1f)) { vm.call({ it.notesStart("meeting") }) }
                }
                Text("Lecture listens through the laptop mic; meeting records the laptop's audio too.",
                     color = Text2, fontSize = 12.5.sp, modifier = Modifier.padding(top = 8.dp))
            }
        }
    }
    Spacer(Modifier.height(10.dp))
    LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        items(items, key = { it.optInt("id") }) { n ->
            Card(Modifier.clickable { vm.call({ it.note(n.optInt("id")) }, open) }) {
                Text(n.str("title"), color = Text1, fontWeight = FontWeight.SemiBold)
                if (n.str("summary").isNotBlank()) Text(n.str("summary"), color = Text2, fontSize = 13.sp, maxLines = 3)
                Label("${n.str("started").replace("T", " ").take(16)} · ${n.str("kind")} · ${n.optInt("words")} words")
            }
        }
    }
}

@Composable
private fun NoteDetail(vm: MainViewModel, n: JSONObject, back: () -> Unit) {
    var view by remember { mutableIntStateOf(0) }            // 0 notes, 1 summary, 2 transcript
    var summary by remember { mutableStateOf(n.str("summary_md")) }
    var busy by remember { mutableStateOf(false) }
    var err by remember { mutableStateOf("") }
    fun summarize(refresh: Boolean) {
        if (busy) return
        busy = true
        err = ""
        vm.call({ api -> runCatching { api.noteSummary(n.optInt("id"), refresh) } }) { r ->
            busy = false
            r.onSuccess { summary = it }.onFailure { err = "Couldn't write the summary: ${it.message}" }
        }
    }
    LaunchedEffect(view) { if (view == 1 && summary.isBlank()) summarize(false) }
    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = back) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back", tint = Cyan) }
            Column {
                Text(n.str("title"), color = Text1, fontWeight = FontWeight.SemiBold, fontSize = 17.sp)
                Label("${n.str("kind")} · ${n.optInt("words")} words")
            }
        }
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth().padding(vertical = 8.dp)) {
            listOf("Notes", "Summary", "Transcript").forEachIndexed { i, label ->
                SegmentedButton(selected = view == i, onClick = { view = i },
                                shape = SegmentedButtonDefaults.itemShape(i, 3)) { Text(label) }
            }
        }
        if (view == 1 && err.isNotBlank()) Text(err, color = Red, fontSize = 13.sp)
        if (view == 1 && busy) Text("✍ Max is writing a short summary…", color = Text2)
        LazyColumn {
            val md = when (view) { 1 -> if (busy) "" else summary; 2 -> n.str("transcript_md"); else -> n.str("notes_md") }
            items(md.lines()) { MarkdownLine(it) }
            if (view == 1 && !busy && summary.isNotBlank()) item {
                GhostButton("↻ Regenerate", Modifier.padding(vertical = 12.dp)) { summarize(true) }
            }
        }
    }
}

/** Headings, bullets and **bold** from Max's notes. */
@Composable
private fun MarkdownLine(raw: String) {
    val line = raw.trimEnd()
    when {
        line.isBlank() -> Spacer(Modifier.height(6.dp))
        line.startsWith("#") -> Text(line.trimStart('#', ' '), color = Cyan, fontWeight = FontWeight.SemiBold,
                                     fontSize = if (line.startsWith("# ")) 19.sp else 16.sp,
                                     modifier = Modifier.padding(top = 10.dp, bottom = 4.dp))
        else -> {
            val bullet = Regex("^\\s*[-*]\\s+").find(line)
            val body = if (bullet != null) line.substring(bullet.range.last + 1) else line
            Text(buildAnnotatedString {
                if (bullet != null) append("•  ")
                var last = 0
                Regex("\\*\\*([^*]+)\\*\\*").findAll(body).forEach { m ->
                    append(body.substring(last, m.range.first))
                    withStyle(SpanStyle(fontWeight = FontWeight.Bold, color = Text1)) { append(m.groupValues[1]) }
                    last = m.range.last + 1
                }
                append(body.substring(last))
            }, color = Text2, fontSize = 14.sp, lineHeight = 20.sp,
               modifier = Modifier.padding(start = if (bullet != null) 6.dp else 0.dp, bottom = 3.dp))
        }
    }
}

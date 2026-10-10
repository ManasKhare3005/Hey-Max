package com.manaskhare.max.ui

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.produceState
import androidx.core.content.ContextCompat
import com.manaskhare.max.Hub
import com.manaskhare.max.Link
import com.manaskhare.max.LocalDb
import com.manaskhare.max.LocalRecording
import com.manaskhare.max.MainViewModel
import com.manaskhare.max.RecordService
import com.manaskhare.max.Sync
import com.manaskhare.max.objects
import com.manaskhare.max.str
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** Facts Max remembers, plus meeting & lecture notes (record from here too). */
@Composable
fun MemoryScreen(vm: MainViewModel, startTab: Int = 0) {
    var tab by remember { mutableIntStateOf(startTab) }
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
    val link by Hub.link.collectAsState()
    val online = link == Link.ONLINE
    LaunchedEffect(rev, online) { if (online) vm.quiet({ it.notes() }) { items = it.objects() } }
    LaunchedEffect(online) {
        while (online) {                  // live recording status (on the laptop)
            vm.quiet({ it.notesStatus() }) { status = it }
            delay(1000)
        }
    }

    val s = status
    // One scrolling list: the record and document cards scroll away so past notes get the screen
    LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp), contentPadding = PaddingValues(bottom = 16.dp)) {
        item { PhoneRecorder(vm, open) }
        item {
            Card {
                when {
                    !online -> {
                        Label("record on the laptop")
                        Text("The laptop is offline: record on this phone above. Its notes are written when the laptop is back.",
                             color = Text2, fontSize = 12.5.sp, modifier = Modifier.padding(top = 6.dp))
                    }
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
        }
        if (online) item { Documents(vm) { rev++ } }
        if (items.isNotEmpty()) item { Label("saved notes", modifier = Modifier.padding(start = 4.dp, top = 6.dp)) }
        items(items, key = { it.optInt("id") }) { n ->
            Card(Modifier.clickable { vm.call({ it.note(n.optInt("id")) }, open) }) {
                Text(n.str("title"), color = Text1, fontWeight = FontWeight.SemiBold)
                if (n.str("summary").isNotBlank()) Text(n.str("summary"), color = Text2, fontSize = 13.sp, maxLines = 3)
                Label("${n.str("started").replace("T", " ").take(16)} · ${n.str("kind")} · ${n.optInt("words")} words")
            }
        }
    }
}

/**
 * Record a lecture or meeting on the phone itself (works with the laptop off): the audio waits on
 * the phone and is uploaded when the laptop is back, which writes the notes (then the phone's copy
 * of the audio is deleted).
 */
@Composable
private fun PhoneRecorder(vm: MainViewModel, open: (JSONObject) -> Unit) {
    val context = LocalContext.current
    val db = remember { LocalDb.get(context) }
    val rev by LocalDb.revision.collectAsState()
    val recordings by produceState(initialValue = emptyList<LocalRecording>(), rev) {
        val week = (System.currentTimeMillis() - 7 * 86_400_000L).toString()
        value = withContext(Dispatchers.IO) { db.recordings("state != 'done' OR started_ms > ?", week) }
    }
    PhoneRecorderCard(vm, open, recordings)
}

@Composable
internal fun PhoneRecorderCard(vm: MainViewModel, open: (JSONObject) -> Unit, recordings: List<LocalRecording>) {
    val context = LocalContext.current
    val db = remember { LocalDb.get(context) }
    val active by RecordService.active.collectAsState()
    val startError by RecordService.error.collectAsState()
    var title by remember { mutableStateOf("") }
    var askedKind by remember { mutableStateOf("lecture") }
    val start = { kind: String -> RecordService.start(context, kind, title); title = "" }
    val askMic = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { got ->
        if (got[Manifest.permission.RECORD_AUDIO] == true) start(askedKind)
    }
    val record = { kind: String ->
        askedKind = kind
        val perms = listOf(Manifest.permission.RECORD_AUDIO) +
            if (Build.VERSION.SDK_INT >= 33) listOf(Manifest.permission.POST_NOTIFICATIONS) else emptyList()
        if (perms.all { ContextCompat.checkSelfPermission(context, it) == PackageManager.PERMISSION_GRANTED }) start(kind)
        else askMic.launch(perms.toTypedArray())
    }
    var tick by remember { mutableIntStateOf(0) }
    LaunchedEffect(active) { while (active != null) { delay(1000); tick++ } }

    Card {
        val a = active
        if (a != null) {
            tick.let { }
            val secs = (System.currentTimeMillis() - a.startedMs) / 1000
            Label("● recording on this phone", color = Red)
            Text("${a.title} · ${secs / 60}:${"%02d".format(secs % 60)}", color = Text1)
            Text("Keeps going with the screen off. Notes are written on the laptop.", color = Text2, fontSize = 12.5.sp)
            Spacer(Modifier.height(8.dp))
            GhostButton("Stop", color = Red) { RecordService.stop() }
        } else {
            Label("record on this phone")
            Spacer(Modifier.height(6.dp))
            Field(title, { title = it }, "Title (optional), e.g. CSE 572 week 7")
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                GhostButton("● Lecture", Modifier.weight(1f)) { record("lecture") }
                GhostButton("● Meeting", Modifier.weight(1f)) { record("meeting") }
            }
            Text("Works with the laptop off. The phone hears its own mic only (not other apps' calls).",
                 color = Text2, fontSize = 12.5.sp, modifier = Modifier.padding(top = 8.dp))
            if (startError.isNotBlank()) Text(startError, color = Red, fontSize = 12.5.sp)
        }
        recordings.filter { it.state != "recording" }.forEach { r ->
            RowDividerThin()
            Row(verticalAlignment = Alignment.CenterVertically,
                modifier = if (r.state == "done" && r.noteId > 0) Modifier.clickable { vm.call({ it.note(r.noteId) }, open) } else Modifier) {
                Column(Modifier.weight(1f).padding(vertical = 6.dp)) {
                    Text(r.title, color = Text1)
                    Label("${r.durationS / 60} min · " + when (r.state) {
                        "waiting" -> "waiting for the laptop"
                        "uploading" -> "sending to the laptop…"
                        "processing" -> "laptop is writing the notes…"
                        "done" -> "notes ready · tap to open"
                        "failed" -> "failed: ${r.error}"
                        else -> r.state
                    }, color = when (r.state) { "done" -> Green; "failed" -> Red; else -> Text2 })
                }
                if (r.state == "failed") GhostButton("Retry") {
                    db.put(r.copy(state = "waiting", error = "")); Sync.trigger(context, 0)
                }
                if (r.state == "waiting" || r.state == "failed") IconButton(onClick = {
                    java.io.File(r.file).delete(); db.delete("recordings", r.uid)
                }) { Icon(Icons.Filled.Delete, contentDescription = "Delete recording", tint = Dim) }
            }
        }
    }
}

@Composable
private fun RowDividerThin() = androidx.compose.material3.HorizontalDivider(
    Modifier.padding(top = 8.dp), thickness = 1.dp, color = Accent0.copy(alpha = 0.10f))

private val DOC_TYPES = arrayOf(
    "application/pdf", "text/plain", "text/markdown",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
)

private fun displayName(context: Context, uri: Uri): String? =
    context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
        if (c.moveToFirst()) c.getString(0) else null
    }

/** Course documents: add a file from the phone (saved to the laptop's course folder) and get notes for it. */
@Composable
private fun Documents(vm: MainViewModel, changed: () -> Unit) {
    val context = LocalContext.current
    var info by remember { mutableStateOf<JSONObject?>(null) }
    var rev by remember { mutableIntStateOf(0) }
    var busy by remember { mutableStateOf("") }
    var err by remember { mutableStateOf("") }
    LaunchedEffect(rev) {
        while (true) {                    // faster while Max is working through files
            vm.call({ it.documents() }) { new ->
                val wasActive = info?.optJSONObject("status")?.optBoolean("active") == true
                info = new
                if (wasActive && new.optJSONObject("status")?.optBoolean("active") != true) changed()
            }
            delay(if (info?.optJSONObject("status")?.optBoolean("active") == true) 3000 else 20000)
        }
    }
    val pick = rememberLauncherForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
        if (uris.isEmpty()) return@rememberLauncherForActivityResult
        busy = "Sending ${uris.size} file${if (uris.size > 1) "s" else ""} to the laptop…"
        err = ""
        vm.call({ api ->
            runCatching {
                for (u in uris) {
                    val (name, bytes) = withContext(Dispatchers.IO) {
                        (displayName(context, u) ?: "document.pdf") to
                            (context.contentResolver.openInputStream(u)?.use { it.readBytes() } ?: ByteArray(0))
                    }
                    api.uploadDoc(name, bytes)
                }
            }
        }) { r ->
            busy = ""
            r.onFailure { err = "Couldn't add it: ${it.message}" }
            rev++
        }
    }

    val i = info ?: return
    val files = i.optJSONArray("files")?.objects() ?: emptyList()
    val pending = files.filter { !it.optBoolean("done") }
    val s = i.optJSONObject("status") ?: JSONObject()
    val active = s.optBoolean("active")
    Card {
        Label("course documents")
        Text("${files.size} files · ${files.size - pending.size} with notes", color = Text1)
        if (active) {
            val waiting = s.optJSONArray("waiting")?.length() ?: 0
            Text("✍ Writing notes for ${s.str("current")}" + if (waiting > 0) " · $waiting waiting" else "",
                 color = Text2, fontSize = 13.sp)
        }
        if (busy.isNotBlank()) Text(busy, color = Text2, fontSize = 13.sp)
        if (err.isNotBlank()) Text(err, color = Red, fontSize = 13.sp)
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            GhostButton("+ Add document", Modifier.weight(1f)) { if (busy.isBlank()) pick.launch(DOC_TYPES) }
            if (!active && pending.isNotEmpty()) {
                GhostButton("✍ Notes for ${pending.size}", Modifier.weight(1f)) {
                    vm.call({ it.summarizeDocs(pending.map { f -> f.str("path") }) }) { rev++ }
                }
            }
        }
        Text("PDF, slides, Word or text. Max reads the whole file and writes notes here.",
             color = Text2, fontSize = 12.5.sp, modifier = Modifier.padding(top = 8.dp))
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
            listOf("Notes", "Summary", if (n.str("kind") == "document") "Text" else "Transcript").forEachIndexed { i, label ->
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

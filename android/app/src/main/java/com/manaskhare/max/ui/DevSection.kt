package com.manaskhare.max.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings
import android.widget.Toast
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.SystemUpdate
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import com.manaskhare.max.BuildConfig
import com.manaskhare.max.MainViewModel
import com.manaskhare.max.objects
import com.manaskhare.max.str
import kotlinx.coroutines.delay
import org.json.JSONObject
import java.io.File
import java.text.DateFormat
import java.util.Date

private val WORKING = setOf("queued", "working", "testing", "building")

/** Type a change for Max; Claude Code on the laptop makes it on a branch; approve or reject here. */
@Composable
fun ChangeMaxSection(vm: MainViewModel) {
    var text by remember { mutableStateOf("") }
    var jobs by remember { mutableStateOf<List<JSONObject>>(emptyList()) }
    var rev by remember { mutableIntStateOf(0) }
    var err by remember { mutableStateOf("") }
    val latest = jobs.firstOrNull()
    val active = latest != null && latest.str("status") in WORKING
    LaunchedEffect(rev) {
        while (true) {
            vm.call({ api -> runCatching { api.devJobs() } }) { r ->
                r.onSuccess { jobs = it.objects(); err = "" }.onFailure { err = it.message ?: "" }
            }
            delay(if (jobs.firstOrNull()?.str("status") in WORKING) 2500 else 15000)
        }
    }

    Section("Change Max") {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("Describe a change. Claude makes it on the laptop, runs the tests, then asks for your OK here.",
                 color = Text2, fontSize = 13.sp)
            OutlinedTextField(
                value = text, onValueChange = { text = it }, modifier = Modifier.fillMaxWidth().heightIn(min = 96.dp),
                placeholder = { Text("e.g. make the Notes list show the course name", color = Dim, fontSize = 14.sp) },
                shape = RoundedCornerShape(10.dp), enabled = !active,
                colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Line, focusedBorderColor = Cyan),
            )
            GhostButton(if (active) "Claude is working…" else "Send to Claude", Modifier.fillMaxWidth()) {
                if (active || text.isBlank()) return@GhostButton
                vm.call({ api -> runCatching { api.devRequest(text) } }) { r ->
                    r.onSuccess { text = ""; err = ""; rev++ }.onFailure { err = it.message ?: "Couldn't send it" }
                }
            }
            if (err.isNotBlank()) Text(err, color = Red, fontSize = 13.sp)
            latest?.let { JobCard(vm, it) { rev++ } }
            jobs.drop(1).take(3).forEach { j ->
                Text("#${j.optInt("id")} · ${j.str("status")} · ${j.str("request").take(60)}", color = Dim, fontSize = 12.sp)
            }
        }
    }
}

@Composable
private fun JobCard(vm: MainViewModel, j: JSONObject, changed: () -> Unit) {
    val status = j.str("status")
    val color = when (status) { "ready" -> Cyan; "failed", "rejected" -> Red; "merged" -> Accent0; else -> Text1 }
    Card {
        Label("#${j.optInt("id")} · ${status.uppercase()}", color = color)
        Text(j.str("request"), color = Text1, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
        val steps = j.optJSONArray("progress")
        if (status in WORKING && steps != null && steps.length() > 0) {
            Text("▸ ${steps.getString(steps.length() - 1)}", color = Text2, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
        }
        if (j.str("summary").isNotBlank() && status !in WORKING) {
            Text(j.str("summary"), color = Text2, fontSize = 13.sp, modifier = Modifier.padding(top = 6.dp))
        }
        if (status == "ready" || status == "merged") {
            val files = j.optJSONArray("files")?.length() ?: 0
            Text("${if (j.optBoolean("tests_ok")) "✓" else "✗"} tests: ${j.str("tests").lines().last()} · $files files" +
                 if (j.optBoolean("apk")) " · new phone app" else "",
                 color = if (j.optBoolean("tests_ok")) Text2 else Red, fontSize = 12.5.sp, modifier = Modifier.padding(top = 6.dp))
        }
        if (j.str("error").isNotBlank()) Text(j.str("error"), color = Red, fontSize = 12.5.sp, modifier = Modifier.padding(top = 6.dp))
        if (status == "ready") {
            Row(Modifier.padding(top = 10.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                GhostButton("Approve", Modifier.weight(1f)) { vm.call({ it.devDecide(j.optInt("id"), "approve") }) { changed() } }
                GhostButton("Reject", Modifier.weight(1f), color = Red) { vm.call({ it.devDecide(j.optInt("id"), "reject") }) { changed() } }
            }
            if (j.optBoolean("apk")) Text("After approving, install the update below.", color = Dim, fontSize = 12.sp,
                                          modifier = Modifier.padding(top = 6.dp))
        } else if (status in WORKING) {
            GhostButton("Cancel", Modifier.padding(top = 8.dp), color = Red) {
                vm.call({ it.devDecide(j.optInt("id"), "reject") }) { changed() }
            }
        }
    }
}

/** Install the phone app the laptop last built (downloaded over the same Tailscale link). */
@Composable
fun UpdateSection(vm: MainViewModel) {
    val context = LocalContext.current
    var info by remember { mutableStateOf<JSONObject?>(null) }
    var busy by remember { mutableStateOf("") }
    LaunchedEffect(Unit) {
        while (true) {
            vm.call({ api -> runCatching { api.appInfo() } }) { r -> r.onSuccess { info = it } }
            delay(30000)
        }
    }
    val fmt = DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT)
    val laptop = info?.optLong("built") ?: 0L
    val newer = info?.optBoolean("available") == true && laptop > BuildConfig.BUILD_TIME + 60_000
    Section("App update") {
        SettingRow(
            when { busy.isNotBlank() -> busy; newer -> "Install update"; else -> "Up to date" }, Icons.Filled.SystemUpdate,
            subtitle = "This app: ${fmt.format(Date(BuildConfig.BUILD_TIME))}" +
                if (laptop > 0) "\nOn the laptop: ${fmt.format(Date(laptop))}" else "",
            color = if (newer) Cyan else Text1,
            onClick = {
                if (busy.isNotBlank() || info?.optBoolean("available") != true) return@SettingRow
                if (!context.packageManager.canRequestPackageInstalls()) {
                    toast(context, "Allow Max to install updates, then tap again")
                    context.startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}")))
                    return@SettingRow
                }
                busy = "Downloading…"
                val file = File(context.cacheDir, "updates/max.apk").apply { parentFile?.mkdirs() }
                vm.call({ api -> runCatching { api.download("/api/app/apk", file) } }) { r ->
                    busy = ""
                    r.onSuccess { install(context, file) }.onFailure { toast(context, "Download failed: ${it.message}") }
                }
            },
        )
    }
}

private fun install(context: Context, apk: File) {
    val uri = FileProvider.getUriForFile(context, "${context.packageName}.files", apk)
    context.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
}

private fun toast(context: Context, msg: String) = Toast.makeText(context, msg, Toast.LENGTH_LONG).show()

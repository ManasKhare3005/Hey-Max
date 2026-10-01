package com.manaskhare.max.ui

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.LifecycleResumeEffect
import com.manaskhare.max.Hub
import com.manaskhare.max.MainViewModel
import com.manaskhare.max.MaxTileService
import com.manaskhare.max.MaxWidget
import android.app.StatusBarManager
import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.graphics.drawable.Icon
import android.os.Build
import com.manaskhare.max.R

@SuppressLint("BatteryLife")
@Composable
fun SettingsScreen(vm: MainViewModel, scanQr: () -> Unit) {
    val context = LocalContext.current
    val paired by vm.paired.collectAsState()
    val link by Hub.link.collectAsState()
    var url by remember { mutableStateOf(vm.prefs.url) }
    var token by remember { mutableStateOf("") }
    var speak by remember { mutableStateOf(vm.prefs.speakReplies) }
    var test by remember { mutableStateOf("") }
    val power = context.getSystemService(PowerManager::class.java)
    var unrestricted by remember { mutableStateOf(power.isIgnoringBatteryOptimizations(context.packageName)) }
    // Re-check whenever you come back (e.g. from the system battery prompt or Samsung's settings)
    LifecycleResumeEffect(Unit) {
        unrestricted = power.isIgnoringBatteryOptimizations(context.packageName)
        onPauseOrDispose { }
    }

    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { Row(Modifier.padding(top = 14.dp)) { Title("Settings") } }
        item {
            Card {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Label("laptop", Modifier.weight(1f))
                    LinkChip(link)
                }
                Spacer(Modifier.height(6.dp))
                Text(if (paired) vm.prefs.url else "Not paired yet", color = Text1, fontSize = 14.sp)
                Spacer(Modifier.height(10.dp))
                Text("On the laptop open the dashboard → Phone tab, then scan its QR code.", color = Text2, fontSize = 13.sp)
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    GhostButton(if (paired) "Re-pair (scan QR)" else "Pair (scan QR)", onClick = scanQr)
                    if (paired) GhostButton("Test") {
                        test = "…"
                        vm.call({ it.ping() }) { test = "✓ ${it.optString("name")} is reachable (${it.optString("mode")} mode)" }
                    }
                }
                if (test.isNotBlank()) Text(test, color = if (test.startsWith("✓")) Green else Text2, fontSize = 13.sp)
            }
        }
        item {
            Card {
                Label("pair manually")
                Spacer(Modifier.height(6.dp))
                Field(url, { url = it }, "https://laptop.tailnet.ts.net")
                Spacer(Modifier.height(6.dp))
                Field(token, { token = it }, "Token (dashboard → Phone → show)")
                Spacer(Modifier.height(8.dp))
                GhostButton("Save") { if (url.isNotBlank() && token.length >= 20) vm.pairManually(url, token) }
            }
        }
        item {
            Card {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Speak replies in Max's voice", color = Text1, modifier = Modifier.weight(1f))
                    Switch(checked = speak, onCheckedChange = { speak = it; vm.prefs.speakReplies = it },
                           colors = SwitchDefaults.colors(checkedTrackColor = Cyan))
                }
            }
        }
        item {
            Card {
                Label("quick access")
                Spacer(Modifier.height(6.dp))
                Text("Talk to Max without opening the app: it slides up over whatever you're doing and listens.",
                     color = Text2, fontSize = 13.sp)
                Spacer(Modifier.height(8.dp))
                GhostButton("Make Max my assistant (side key)", Modifier.fillMaxWidth()) {
                    runCatching { context.startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS)) }
                }
                Text("Pick “Digital assistant app” → Max. Then Settings → Advanced features → Side button → " +
                     "Press and hold → Digital assistant.", color = Text2, fontSize = 12.sp)
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    GhostButton("Add Quick Settings tile") {
                        if (Build.VERSION.SDK_INT >= 33) {
                            context.getSystemService(StatusBarManager::class.java).requestAddTileService(
                                ComponentName(context, MaxTileService::class.java), "Max",
                                Icon.createWithResource(context, R.drawable.ic_stat_max), context.mainExecutor) { }
                        }
                    }
                    GhostButton("Add widget") {
                        val wm = context.getSystemService(AppWidgetManager::class.java)
                        if (wm.isRequestPinAppWidgetSupported) wm.requestPinAppWidget(ComponentName(context, MaxWidget::class.java), null, null)
                    }
                }
                Text("Also: long-press the Max icon → “Talk to Max”.", color = Text2, fontSize = 12.sp)
            }
        }
        item {
            Card {
                Label("laptop controls")
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    GhostButton("Listen now") { vm.call({ it.control("listen") }) }
                    GhostButton("Pause mic") { vm.call({ it.control("pause") }) }
                    GhostButton("Resume") { vm.call({ it.control("resume") }) }
                }
                Text("“Listen now” makes the laptop listen as if you said “Hey Max”.", color = Text2, fontSize = 12.sp)
            }
        }
        item {
            Card {
                Label("stay connected")
                Spacer(Modifier.height(6.dp))
                Text(if (unrestricted) "✓ Battery optimisation is off for Max: notifications arrive reliably."
                     else "Samsung may close Max in the background and delay reminders. Allow it to run unrestricted " +
                          "(or: Settings → Apps → Max → Battery → Unrestricted).",
                     color = if (unrestricted) Green else Text2, fontSize = 13.sp)
                if (!unrestricted) {
                    Spacer(Modifier.height(8.dp))
                    GhostButton("Allow background") {
                        runCatching {
                            context.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                                                         Uri.parse("package:${context.packageName}")))
                        }.onFailure {      // some Samsung builds hide that prompt: open the app's own settings
                            context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                                         Uri.parse("package:${context.packageName}")))
                        }
                    }
                }
            }
        }
        if (paired) item {
            GhostButton("Unpair this phone", Modifier.fillMaxWidth(), color = Red) { vm.unpair(); test = "" }
        }
        item { Spacer(Modifier.height(20.dp)) }
    }
}

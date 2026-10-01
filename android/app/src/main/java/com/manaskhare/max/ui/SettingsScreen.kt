package com.manaskhare.max.ui

import android.Manifest
import android.annotation.SuppressLint
import android.content.pm.PackageManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Contacts
import androidx.compose.material.icons.filled.NotificationsActive
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import android.app.StatusBarManager
import android.appwidget.AppWidgetManager
import android.content.ComponentName
import android.content.Intent
import android.graphics.drawable.Icon
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.BatteryChargingFull
import androidx.compose.material.icons.filled.Hearing
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.LinkOff
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.Palette
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.QrCodeScanner
import androidx.compose.material.icons.filled.SettingsInputAntenna
import androidx.compose.material.icons.filled.SmartButton
import androidx.compose.material.icons.filled.TouchApp
import androidx.compose.material.icons.filled.Widgets
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.LifecycleResumeEffect
import com.manaskhare.max.Hub
import com.manaskhare.max.MainViewModel
import com.manaskhare.max.MaxTileService
import com.manaskhare.max.MaxWidget
import com.manaskhare.max.PhoneActions
import com.manaskhare.max.R

@SuppressLint("BatteryLife")
@Composable
fun SettingsScreen(vm: MainViewModel, scanQr: () -> Unit) {
    var appearance by rememberSaveable { mutableStateOf(false) }
    if (appearance) {
        BackHandler { appearance = false }
        AppearanceScreen(onBack = { appearance = false })
        return
    }

    val context = LocalContext.current
    val look = LocalLook.current
    val paired by vm.paired.collectAsState()
    val link by Hub.link.collectAsState()
    var url by remember { mutableStateOf(vm.prefs.url) }
    var token by remember { mutableStateOf("") }
    var manual by remember { mutableStateOf(false) }
    var speak by remember { mutableStateOf(vm.prefs.speakReplies) }
    var test by remember { mutableStateOf("") }
    val ignoring = {
        runCatching { context.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(context.packageName) }
            .getOrDefault(false)
    }
    var unrestricted by remember { mutableStateOf(ignoring()) }
    // Re-check whenever you come back (e.g. from the system battery prompt or Samsung's settings)
    LifecycleResumeEffect(Unit) {
        unrestricted = ignoring()
        onPauseOrDispose { }
    }
    val toast = { msg: String -> Toast.makeText(context, msg, Toast.LENGTH_SHORT).show() }
    val granted = { perm: String -> ContextCompat.checkSelfPermission(context, perm) == PackageManager.PERMISSION_GRANTED }
    var contacts by remember { mutableStateOf(granted(Manifest.permission.READ_CONTACTS)) }
    var calls by remember { mutableStateOf(granted(Manifest.permission.CALL_PHONE)) }
    var notifs by remember { mutableStateOf(PhoneActions.notificationAccess(context)) }
    var overlay by remember { mutableStateOf(Settings.canDrawOverlays(context)) }
    LifecycleResumeEffect(Unit) {
        contacts = granted(Manifest.permission.READ_CONTACTS); calls = granted(Manifest.permission.CALL_PHONE)
        notifs = PhoneActions.notificationAccess(context); overlay = Settings.canDrawOverlays(context)
        onPauseOrDispose { }
    }
    val askPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        contacts = granted(Manifest.permission.READ_CONTACTS); calls = granted(Manifest.permission.CALL_PHONE)
    }
    val openSettings = { action: String, withPackage: Boolean ->
        runCatching {
            context.startActivity(if (withPackage) Intent(action, Uri.parse("package:${context.packageName}")) else Intent(action))
        }.onFailure { toast("Open your phone's settings and search for Max") }
    }

    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(22.dp)) {
        item { Row(Modifier.padding(start = 10.dp, top = 22.dp)) { Title("Settings") } }

        item {
            Section("Laptop") {
                SettingRow(if (paired) "Paired" else "Not paired yet", Icons.Filled.Link,
                           subtitle = if (paired) vm.prefs.url.removePrefix("https://") else "Dashboard → Phone tab shows a QR code",
                           trailing = { LinkChip(link) })
                RowDivider()
                SettingRow(if (paired) "Re-pair (scan QR code)" else "Pair (scan QR code)", Icons.Filled.QrCodeScanner, onClick = scanQr)
                if (paired) {
                    RowDivider()
                    SettingRow("Test connection", Icons.Filled.SettingsInputAntenna,
                               subtitle = test.ifBlank { null }, onClick = {
                        test = "Checking…"
                        vm.call({ it.ping() }) { test = "✓ ${it.optString("name")} is reachable (${it.optString("mode")} mode)" }
                    })
                }
                RowDivider()
                SettingRow("Pair manually", Icons.Filled.Edit, subtitle = "Type the address and token instead",
                           onClick = { manual = !manual })
                if (manual) {
                    Column(Modifier.padding(start = 16.dp, end = 16.dp, bottom = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Field(url, { url = it }, "https://laptop.tailnet.ts.net")
                        Field(token, { token = it }, "Token (dashboard → Phone → show)")
                        GhostButton("Save", Modifier.fillMaxWidth()) {
                            if (url.isNotBlank() && token.length >= 20) { vm.pairManually(url, token); manual = false }
                            else toast("Enter the address and the full token")
                        }
                    }
                }
            }
        }

        item {
            Section("Look & sound") {
                SettingRow("Appearance", Icons.Filled.Palette,
                           subtitle = "${look.accent.label} · ${look.piece.label} · ${look.font.label} font",
                           onClick = { appearance = true })
                RowDivider()
                SettingRow("Speak replies", Icons.AutoMirrored.Filled.VolumeUp, subtitle = "In Max's own voice",
                           trailing = { Toggle(speak) { speak = it; vm.prefs.speakReplies = it } })
            }
        }

        item {
            Column {
                Label("Laptop controls", Modifier.padding(start = 6.dp, bottom = 8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    ControlTile("Listen now", Icons.Filled.Hearing, Modifier.weight(1f)) { vm.call({ it.control("listen") }) { toast("The laptop is listening") } }
                    ControlTile("Pause mic", Icons.Filled.MicOff, Modifier.weight(1f)) { vm.call({ it.control("pause") }) { toast("Laptop mic paused") } }
                    ControlTile("Resume", Icons.Filled.PlayArrow, Modifier.weight(1f)) { vm.call({ it.control("resume") }) { toast("Laptop mic on") } }
                }
                Text("“Listen now” makes the laptop listen as if you said “Hey Max”.", color = Text2, fontSize = 12.5.sp,
                     modifier = Modifier.padding(start = 6.dp, top = 8.dp))
            }
        }

        item {
            Section("Phone powers") {
                SettingRow("Contacts", Icons.Filled.Contacts,
                           subtitle = if (contacts) "✓ “Call Mom”, “text Alex” find the right person" else "So Max knows who “Mom” is",
                           onClick = if (contacts) null else ({ askPermission.launch(Manifest.permission.READ_CONTACTS) }),
                           trailing = if (contacts) ({ Check() }) else null)
                RowDivider()
                SettingRow("Phone calls", Icons.Filled.Call,
                           subtitle = if (calls) "✓ Calls start after you say yes" else "Without this, Max dials and you tap Call",
                           onClick = if (calls) null else ({ askPermission.launch(Manifest.permission.CALL_PHONE) }),
                           trailing = if (calls) ({ Check() }) else null)
                RowDivider()
                SettingRow("Notifications", Icons.Filled.NotificationsActive,
                           subtitle = if (notifs) "✓ “What did I miss?” works (kept in memory only)" else "Lets Max read your recent notifications",
                           onClick = { openSettings(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS, false) },
                           trailing = if (notifs) ({ Check() }) else null)
                RowDivider()
                SettingRow("Act while the phone is locked away", Icons.AutoMirrored.Filled.OpenInNew,
                           subtitle = if (overlay) "✓ Asks from the laptop happen right away"
                                      else "“Display over other apps”: otherwise you get a tap-to-open notification",
                           onClick = { openSettings(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, true) },
                           trailing = if (overlay) ({ Check() }) else null)
            }
        }

        item {
            Section("Quick access") {
                SettingRow("Add Quick Settings tile", Icons.Filled.TouchApp, subtitle = "Pull down the shade, tap Max, talk", onClick = {
                    if (Build.VERSION.SDK_INT >= 33) {
                        context.getSystemService(StatusBarManager::class.java).requestAddTileService(
                            ComponentName(context, MaxTileService::class.java), "Max",
                            Icon.createWithResource(context, R.drawable.ic_stat_max), context.mainExecutor) { }
                    } else toast("Edit your Quick Settings and drag the Max tile in")
                })
                RowDivider()
                SettingRow("Add home-screen widget", Icons.Filled.Widgets, subtitle = "A “Talk to Max” button", onClick = {
                    val wm = context.getSystemService(AppWidgetManager::class.java)
                    if (wm.isRequestPinAppWidgetSupported) wm.requestPinAppWidget(ComponentName(context, MaxWidget::class.java), null, null)
                    else toast("Long-press your home screen → Widgets → Max")
                })
                RowDivider()
                SettingRow("Side key", Icons.Filled.SmartButton,
                           subtitle = "Make Max the digital assistant, then Side button → Press and hold → Digital assistant",
                           onClick = { runCatching { context.startActivity(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS)) } })
            }
        }

        item {
            Section("Stay connected") {
                SettingRow(if (unrestricted) "Background: unrestricted" else "Allow background",
                           Icons.Filled.BatteryChargingFull,
                           subtitle = if (unrestricted) "Reminders and approvals arrive reliably"
                                      else "Samsung may close Max and delay reminders",
                           color = if (unrestricted) Text1 else Amber,
                           onClick = if (unrestricted) null else ({
                               runCatching {
                                   context.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                                                                Uri.parse("package:${context.packageName}")))
                               }.onFailure {      // some Samsung builds hide that prompt: open the app's own settings
                                   context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                                                Uri.parse("package:${context.packageName}")))
                               }
                           }))
            }
        }

        if (paired) item {
            Section("Danger zone") {
                SettingRow("Unpair this phone", Icons.Filled.LinkOff, color = Red, onClick = { vm.unpair(); test = "" })
            }
        }
        item { Spacer(Modifier.height(20.dp)) }
    }
}

@Composable
private fun Check() = Icon(Icons.Filled.CheckCircle, contentDescription = "On", tint = Accent0)

/** Equal-size tile (icon over a one-line label), three to a row. */
@Composable
private fun ControlTile(label: String, icon: ImageVector, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Column(
        modifier.height(84.dp)
            .clip(RoundedCornerShape(18.dp))
            .background(Color.White.copy(alpha = 0.035f))
            .border(1.dp, Accent0.copy(alpha = 0.16f), RoundedCornerShape(18.dp))
            .clickable(role = Role.Button, onClick = onClick)
            .padding(8.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(icon, null, tint = Accent0, modifier = Modifier.size(24.dp))
        Spacer(Modifier.height(8.dp))
        Text(label, color = Text1, fontSize = 13.sp, fontWeight = FontWeight.Medium, maxLines = 1, textAlign = TextAlign.Center)
    }
}

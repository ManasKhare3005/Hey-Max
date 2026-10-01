package com.manaskhare.max

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.Event
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.manaskhare.max.ui.ChatScreen
import com.manaskhare.max.ui.Cyan
import com.manaskhare.max.ui.Dim
import com.manaskhare.max.ui.HomeScreen
import com.manaskhare.max.ui.MaxTheme
import com.manaskhare.max.ui.MemoryScreen
import com.manaskhare.max.ui.Navy
import com.manaskhare.max.ui.Panel
import com.manaskhare.max.ui.PanelHi
import com.manaskhare.max.ui.Red
import com.manaskhare.max.ui.SettingsScreen
import com.manaskhare.max.ui.Text1
import com.manaskhare.max.ui.Text2
import com.manaskhare.max.ui.TodayScreen

class MainActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()
    private var afterMicGrant: (() -> Unit)? = null

    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) afterMicGrant?.invoke() else toast("Max needs the microphone for push-to-talk.")
        afterMicGrant = null
    }
    private val notifPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { }
    private val scanner = registerForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@registerForActivityResult
        toast(if (vm.pair(text)) "Paired with your laptop" else "That isn't a Max pairing code")
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        handleLink(intent)
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            notifPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        MaxApp.startLink(this)

        setContent {
            MaxTheme {
                var tab by remember { mutableIntStateOf(if (vm.prefs.paired) 0 else 4) }
                val approvals by Hub.approvals.collectAsState()
                Scaffold(
                    containerColor = Navy,
                    bottomBar = {
                        NavigationBar(containerColor = Panel) {
                            listOf(
                                Triple("Max", Icons.Filled.GraphicEq, 0),
                                Triple("Chat", Icons.AutoMirrored.Filled.Chat, 1),
                                Triple("Today", Icons.Filled.Event, 2),
                                Triple("Memory", Icons.Filled.Psychology, 3),
                                Triple("Settings", Icons.Filled.Settings, 4),
                            ).forEach { (label, icon, i) ->
                                NavigationBarItem(
                                    selected = tab == i, onClick = { tab = i },
                                    icon = { Icon(icon, contentDescription = label) }, label = { Text(label) },
                                    colors = NavigationBarItemDefaults.colors(
                                        selectedIconColor = Cyan, selectedTextColor = Cyan, indicatorColor = PanelHi,
                                        unselectedIconColor = Dim, unselectedTextColor = Dim),
                                )
                            }
                        }
                    },
                ) { pad ->
                    Box(Modifier.fillMaxSize().padding(pad)) {
                        when (tab) {
                            0 -> HomeScreen(vm, ::withMic)
                            1 -> ChatScreen(vm)
                            2 -> TodayScreen(vm)
                            3 -> MemoryScreen(vm)
                            else -> SettingsScreen(vm, ::scanQr)
                        }
                    }
                }
                // A risky action is waiting for this phone's OK (also shown as a notification)
                approvals.firstOrNull()?.let { a ->
                    AlertDialog(
                        onDismissRequest = {},
                        containerColor = Panel,
                        title = { Text("Max needs your OK", color = Text1) },
                        text = { Text(a.prompt, color = Text2) },
                        confirmButton = { TextButton(onClick = { vm.answer(a.id, true) }) { Text("Approve", color = Cyan) } },
                        dismissButton = { TextButton(onClick = { vm.answer(a.id, false) }) { Text("Deny", color = Red) } },
                    )
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleLink(intent)
    }

    override fun onResume() {
        super.onResume()
        MaxApp.startLink(this)          // reconnects right away if the link dropped
    }

    private fun handleLink(intent: Intent?) {
        val data = intent?.data ?: return
        if (data.scheme == "max") toast(if (vm.pair(data.toString())) "Paired with your laptop" else "Invalid pairing link")
    }

    private fun withMic(action: () -> Unit) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            action()
        } else {
            afterMicGrant = action
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun scanQr() = scanner.launch(ScanOptions()
        .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
        .setPrompt("Scan the QR code in Max's dashboard (Phone tab)")
        .setBeepEnabled(false)
        .setOrientationLocked(false))

    private fun toast(text: String) = Toast.makeText(this, text, Toast.LENGTH_SHORT).show()
}

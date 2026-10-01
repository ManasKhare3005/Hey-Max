package com.manaskhare.max

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeOff
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.manaskhare.max.ui.Cyan
import com.manaskhare.max.ui.Label
import com.manaskhare.max.ui.Line
import com.manaskhare.max.ui.MaxTheme
import com.manaskhare.max.ui.Navy
import com.manaskhare.max.ui.Orb
import com.manaskhare.max.ui.Panel
import com.manaskhare.max.ui.Red
import com.manaskhare.max.ui.Text1
import com.manaskhare.max.ui.Text2
import kotlinx.coroutines.delay

/**
 * The quick listening sheet: slides up over whatever you're doing and starts listening at
 * once. Opened by the side key / assist gesture (Max as the phone's assistant), the Quick
 * Settings tile, the home-screen widget or the app icon's "Talk to Max" shortcut.
 * It's a visible activity, so "mic only while using the app" is enough.
 */
class AssistActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()

    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { ok ->
        if (ok) vm.listen() else { vm.error.value = "Allow the microphone to talk to Max."; }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        MaxApp.startLink(this)
        setContent { MaxTheme { Sheet(vm, onClose = ::finish, openApp = ::openApp) } }
        if (savedInstanceState == null) startListening()
    }

    override fun onNewIntent(intent: Intent) {      // pressed again while the sheet is open
        super.onNewIntent(intent)
        if (vm.phase.value == Phase.SPEAKING) vm.stopSpeaking()
        startListening()
    }

    override fun onStop() {                         // switching away cancels (recorder/player stop with the view model)
        super.onStop()
        if (!isChangingConfigurations) finish()
    }

    private fun startListening() {
        if (!vm.prefs.paired) { vm.error.value = "Pair with your laptop first (open the app → Settings)."; return }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            vm.listen()
        } else {
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    private fun openApp() {
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        finish()
    }
}

@Composable
private fun Sheet(vm: MainViewModel, onClose: () -> Unit, openApp: () -> Unit) {
    val phase by vm.phase.collectAsState()
    val level by vm.level.collectAsState()
    val heard by vm.heard.collectAsState()
    val reply by vm.reply.collectAsState()
    val error by vm.error.collectAsState()
    val approvals by Hub.approvals.collectAsState()
    var wasBusy by remember { mutableStateOf(false) }

    // After Max answers: listen again if it asked a question, otherwise close in a few seconds
    LaunchedEffect(phase, approvals.isEmpty()) {
        if (phase != Phase.IDLE) { wasBusy = true; return@LaunchedEffect }
        if (!wasBusy || approvals.isNotEmpty()) return@LaunchedEffect
        if (reply.trim().endsWith("?") && error.isBlank()) {
            delay(300)
            vm.listen()
        } else {
            delay(if (error.isNotBlank() || heard.isBlank() || heard.startsWith("(")) 7000 else 4500)
            onClose()
        }
    }

    Box(
        Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.35f))
            .clickable(remember { MutableInteractionSource() }, indication = null, onClick = onClose),
        contentAlignment = Alignment.BottomCenter,
    ) {
        Column(
            Modifier.fillMaxWidth()
                .background(Panel, RoundedCornerShape(topStart = 26.dp, topEnd = 26.dp))
                .border(1.dp, Line, RoundedCornerShape(topStart = 26.dp, topEnd = 26.dp))
                .clickable(remember { MutableInteractionSource() }, indication = null) { }   // taps inside don't close
                .navigationBarsPadding()
                .padding(horizontal = 20.dp, vertical = 16.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Box(Modifier.size(width = 36.dp, height = 4.dp).background(Line, RoundedCornerShape(2.dp)))
            Spacer(Modifier.height(4.dp))
            Orb(phase, level, Modifier.size(150.dp))
            Label(when (phase) {
                Phase.IDLE -> if (approvals.isNotEmpty()) "needs your ok" else "tap to talk"
                Phase.LISTENING -> "listening…"
                Phase.THINKING -> "thinking"
                Phase.SPEAKING -> "speaking"
            }, color = Cyan)
            Spacer(Modifier.height(10.dp))
            if (heard.isNotBlank()) Text("“$heard”", color = Text2, fontSize = 15.sp, textAlign = TextAlign.Center)
            if (reply.isNotBlank()) {
                Spacer(Modifier.height(6.dp))
                Text(reply, color = Text1, fontSize = 17.sp, lineHeight = 23.sp, textAlign = TextAlign.Center)
            }
            if (error.isNotBlank()) {
                Spacer(Modifier.height(6.dp))
                Text(error, color = Red, fontSize = 13.sp, textAlign = TextAlign.Center)
            }
            approvals.firstOrNull()?.let { a ->
                Spacer(Modifier.height(12.dp))
                Column(Modifier.fillMaxWidth().border(1.dp, Cyan.copy(alpha = 0.4f), RoundedCornerShape(14.dp)).padding(12.dp)) {
                    Label("max needs your ok", color = Cyan)
                    Text(a.prompt, color = Text1, modifier = Modifier.padding(vertical = 6.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = { vm.answer(a.id, true) }) { Text("Approve", color = Cyan) }
                        TextButton(onClick = { vm.answer(a.id, false) }) { Text("Deny", color = Red) }
                    }
                }
            }
            Spacer(Modifier.height(14.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = openApp) { Text("Open Max", color = Text2) }
                Spacer(Modifier.size(24.dp))
                FilledIconButton(
                    onClick = { vm.toggleMic() }, modifier = Modifier.size(64.dp), shape = CircleShape,
                    colors = IconButtonDefaults.filledIconButtonColors(
                        containerColor = if (phase == Phase.LISTENING) Red else Cyan, contentColor = Navy),
                ) {
                    Icon(when (phase) {
                        Phase.LISTENING -> Icons.Filled.Stop
                        Phase.SPEAKING -> Icons.AutoMirrored.Filled.VolumeOff
                        else -> Icons.Filled.Mic
                    }, contentDescription = "Talk to Max", modifier = Modifier.size(28.dp))
                }
                Spacer(Modifier.size(24.dp))
                TextButton(onClick = onClose) { Text("Close", color = Text2) }
            }
        }
    }
}

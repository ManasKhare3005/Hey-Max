package com.manaskhare.max.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.automirrored.filled.VolumeOff
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manaskhare.max.Hub
import com.manaskhare.max.MainViewModel
import com.manaskhare.max.Phase
import com.manaskhare.max.Turn

@Composable
fun HomeScreen(vm: MainViewModel, askMic: (() -> Unit) -> Unit) {
    val phase by vm.phase.collectAsState()
    val level by vm.level.collectAsState()
    val heard by vm.heard.collectAsState()
    val reply by vm.reply.collectAsState()
    val error by vm.error.collectAsState()
    val link by Hub.link.collectAsState()
    val stage by Hub.stage.collectAsState()
    var text by remember { mutableStateOf("") }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Row(Modifier.fillMaxWidth().padding(top = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("MAX", color = Text1, fontSize = 20.sp, fontWeight = FontWeight.Bold, letterSpacing = 4.sp)
                Label(if (stage.isNotBlank()) "laptop: $stage" else "your laptop assistant")
            }
            LinkChip(link)
        }

        Box(Modifier.weight(1f).fillMaxWidth(), contentAlignment = Alignment.Center) {
            Orb(phase, level, Modifier.size(300.dp))
        }

        Label(when (phase) {
            Phase.IDLE -> "tap to talk"
            Phase.LISTENING -> "listening… tap to send"
            Phase.THINKING -> "thinking"
            Phase.SPEAKING -> "speaking · tap to stop"
        }, color = Cyan)
        Spacer(Modifier.height(10.dp))
        if (heard.isNotBlank()) {
            Text("“$heard”", color = Text2, fontSize = 15.sp, textAlign = TextAlign.Center)
            Spacer(Modifier.height(6.dp))
        }
        if (reply.isNotBlank()) {
            Text(reply, color = Text1, fontSize = 17.sp, textAlign = TextAlign.Center, lineHeight = 23.sp,
                 modifier = Modifier.padding(horizontal = 8.dp))
        }
        if (error.isNotBlank()) {
            Spacer(Modifier.height(6.dp))
            Text(error, color = Red, fontSize = 13.sp, textAlign = TextAlign.Center)
        }
        Spacer(Modifier.height(16.dp))

        FilledIconButton(
            onClick = { if (phase == Phase.IDLE) askMic { vm.toggleMic() } else vm.toggleMic() },
            modifier = Modifier.size(76.dp),
            shape = CircleShape,
            colors = IconButtonDefaults.filledIconButtonColors(
                containerColor = if (phase == Phase.LISTENING) Red else Cyan, contentColor = Navy),
        ) {
            Icon(when (phase) {
                Phase.LISTENING -> Icons.Filled.Stop
                Phase.SPEAKING -> Icons.AutoMirrored.Filled.VolumeOff
                else -> Icons.Filled.Mic
            }, contentDescription = "Talk to Max", modifier = Modifier.size(34.dp))
        }
        Spacer(Modifier.height(16.dp))

        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(bottom = 12.dp)) {
            OutlinedTextField(
                value = text, onValueChange = { text = it }, modifier = Modifier.weight(1f),
                placeholder = { Text("Type to Max…", color = Dim) }, singleLine = true,
                shape = RoundedCornerShape(12.dp),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { vm.send(text); text = "" }),
                colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Line, focusedBorderColor = Cyan),
            )
            IconButton(onClick = { vm.send(text); text = "" }) {
                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send", tint = Cyan)
            }
        }
    }
}

@Composable
fun ChatScreen(vm: MainViewModel) {
    val turns by vm.turns.collectAsState()
    val list = rememberLazyListState()
    LaunchedEffect(Unit) { vm.refreshTurns() }
    LaunchedEffect(turns.size) { if (turns.isNotEmpty()) list.animateScrollToItem(turns.size - 1) }

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        Row(Modifier.fillMaxWidth().padding(vertical = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            Title("Conversation")
            Spacer(Modifier.weight(1f))
            GhostButton("Refresh") { vm.refreshTurns() }
        }
        if (turns.isEmpty()) Text("Nothing yet. Talk to Max from the Home tab.", color = Text2)
        LazyColumn(state = list, verticalArrangement = Arrangement.spacedBy(10.dp), modifier = Modifier.weight(1f)) {
            items(turns) { TurnBubbles(it) }
            item { Spacer(Modifier.height(12.dp)) }
        }
    }
}

@Composable
private fun TurnBubbles(t: Turn) {
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
            Text(t.user, color = Text1, fontSize = 15.sp, modifier = Modifier
                .background(PanelHi, RoundedCornerShape(14.dp, 14.dp, 4.dp, 14.dp))
                .padding(horizontal = 12.dp, vertical = 8.dp))
        }
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.Top) {
            Box(Modifier.padding(top = 10.dp).size(8.dp).background(Cyan, CircleShape))
            Spacer(Modifier.width(8.dp))
            Text(if (t.pending) "…" else t.reply, color = Text1, fontSize = 15.sp, lineHeight = 21.sp,
                 modifier = Modifier
                     .border(1.dp, Line, RoundedCornerShape(4.dp, 14.dp, 14.dp, 14.dp))
                     .background(Panel, RoundedCornerShape(4.dp, 14.dp, 14.dp, 14.dp))
                     .padding(horizontal = 12.dp, vertical = 8.dp))
        }
        if (t.ts.isNotBlank()) Label(t.ts.replace("T", " ").take(16), Modifier.padding(start = 16.dp, top = 3.dp))
    }
}

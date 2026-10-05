package com.manaskhare.max.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.isImeVisible
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.automirrored.filled.VolumeOff
import androidx.compose.material.icons.filled.Keyboard
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manaskhare.max.Hub
import com.manaskhare.max.MainViewModel
import com.manaskhare.max.Phase
import com.manaskhare.max.Turn

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun HomeScreen(vm: MainViewModel, askMic: (() -> Unit) -> Unit) {
    val phase by vm.phase.collectAsState()
    val level by vm.level.collectAsState()
    val heard by vm.heard.collectAsState()
    val reply by vm.reply.collectAsState()
    val error by vm.error.collectAsState()
    val link by Hub.link.collectAsState()
    val look = LocalLook.current
    val words = look.piece == Centrepiece.WORDS
    var typing by remember { mutableStateOf(false) }
    var text by remember { mutableStateOf("") }
    val keyboardOpen = WindowInsets.isImeVisible
    val focus = remember { FocusRequester() }
    LaunchedEffect(typing) { if (typing) focus.requestFocus() }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(start = 26.dp, end = 26.dp, top = 22.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Max", style = DisplayStyle, color = Text1, fontSize = 32.sp, modifier = Modifier.weight(1f))
            LinkChip(link)
        }
        // The orb makes way while typing, so the reply and the text box both fit above the keyboard
        if (!words && !keyboardOpen) Centrepiece(phase, level, Modifier.fillMaxWidth().height(320.dp))

        Column(
            Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 26.dp),
            verticalArrangement = if (words) Arrangement.Center else Arrangement.Top,
        ) {
            if (words) Spacer(Modifier.height(24.dp))
            val prompt = when {
                phase == Phase.LISTENING -> "Listening…"
                phase == Phase.THINKING && heard.isNotBlank() -> "“$heard”"
                heard.isNotBlank() -> "You asked: “$heard”"
                else -> "Tap to talk, or type below."
            }
            Text(prompt, color = Text2, fontSize = 14.sp)
            Spacer(Modifier.height(12.dp))
            val answer = when {
                reply.isNotBlank() -> reply
                phase == Phase.THINKING -> "…"
                heard.isBlank() -> "How can I help?"
                else -> ""
            }
            Text(highlighted(answer), style = DisplayStyle, color = Text1,
                 fontSize = (if (words) look.size.wordsSp else look.size.sp).sp,
                 lineHeight = ((if (words) look.size.wordsSp else look.size.sp) * 1.12f).sp)
            if (error.isNotBlank()) {
                Spacer(Modifier.height(10.dp))
                Text(error, color = Red, fontSize = 13.sp)
            }
            Spacer(Modifier.height(12.dp))
        }

        if (typing) {
            Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = text, onValueChange = { text = it }, modifier = Modifier.weight(1f).focusRequester(focus),
                    placeholder = { Text("Type to Max…", color = Dim) }, singleLine = true, shape = RoundedCornerShape(14.dp),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                    keyboardActions = KeyboardActions(onSend = { vm.send(text); text = ""; typing = false }),
                    colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Line, focusedBorderColor = Accent0,
                                                              focusedContainerColor = Color.White.copy(alpha = 0.04f),
                                                              unfocusedContainerColor = Color.White.copy(alpha = 0.04f)),
                )
                IconButton(onClick = { vm.send(text); text = ""; typing = false }) {
                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send", tint = Accent0)
                }
            }
        }

        Column(Modifier.fillMaxWidth().padding(top = 10.dp, bottom = 14.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            if (words) {
                Box(Modifier.width(170.dp).height(2.dp).background(Brush.horizontalGradient(listOf(Color.Transparent, Accent0, Color.Transparent))))
                Spacer(Modifier.height(14.dp))
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(48.dp))
                Spacer(Modifier.width(12.dp))
                val (label, icon) = when (phase) {
                    Phase.LISTENING -> "Tap to send" to Icons.Filled.Stop
                    Phase.THINKING -> "Thinking…" to null
                    Phase.SPEAKING -> "Tap to stop" to Icons.AutoMirrored.Filled.VolumeOff
                    Phase.IDLE -> "Tap to talk" to Icons.Filled.Mic
                }
                PillButton(label, icon) { if (phase == Phase.IDLE) askMic { vm.toggleMic() } else vm.toggleMic() }
                Spacer(Modifier.width(12.dp))
                IconButton(onClick = { typing = !typing }, modifier = Modifier.size(48.dp)) {
                    Icon(Icons.Filled.Keyboard, contentDescription = "Type instead", tint = if (typing) Accent0 else Dim)
                }
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
        Row(Modifier.fillMaxWidth().padding(start = 10.dp, top = 22.dp, bottom = 12.dp), verticalAlignment = Alignment.CenterVertically) {
            Title("Conversation")
            Spacer(Modifier.weight(1f))
            GhostButton("Refresh") { vm.refreshTurns() }
        }
        if (turns.isEmpty()) Text("Nothing yet. Talk to Max from the Max tab.", color = Text2, modifier = Modifier.padding(10.dp))
        LazyColumn(state = list, verticalArrangement = Arrangement.spacedBy(14.dp), modifier = Modifier.weight(1f)) {
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
                .padding(start = 48.dp)
                .background(PanelHi, RoundedCornerShape(18.dp, 18.dp, 4.dp, 18.dp))
                .padding(horizontal = 14.dp, vertical = 9.dp))
        }
        Spacer(Modifier.height(8.dp))
        Text(if (t.pending) highlighted("…") else highlighted(t.reply), style = DisplayStyle, color = Text1, fontSize = 21.sp, lineHeight = 26.sp,
             modifier = Modifier.padding(end = 32.dp)
                 .border(1.dp, Line, RoundedCornerShape(4.dp, 18.dp, 18.dp, 18.dp))
                 .background(Color.White.copy(alpha = 0.03f), RoundedCornerShape(4.dp, 18.dp, 18.dp, 18.dp))
                 .padding(horizontal = 14.dp, vertical = 10.dp))
        if (t.ts.isNotBlank()) Label(t.ts.replace("T", " ").take(16), Modifier.padding(start = 6.dp, top = 4.dp), color = Dim)
    }
}

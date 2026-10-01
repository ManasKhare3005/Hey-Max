package com.manaskhare.max.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ShortText
import androidx.compose.material.icons.filled.GraphicEq
import androidx.compose.material.icons.filled.RadioButtonChecked
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manaskhare.max.Phase

/** Settings → Appearance: colour, centrepiece, font and options, applied live and saved on the phone. */
@Composable
fun AppearanceScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val look = LocalLook.current
    val set = { change: (Look) -> Look -> Looks.update(context, change) }

    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(22.dp)) {
        item {
            Row(Modifier.padding(top = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "Back to settings", tint = Accent0) }
                Title("Appearance")
            }
        }

        // live preview
        item {
            Column(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(24.dp))
                    .background(Brush.verticalGradient(listOf(look.accent.bgTop, look.accent.base)))
                    .border(1.dp, Accent0.copy(alpha = 0.18f), RoundedCornerShape(24.dp))
                    .padding(18.dp),
            ) {
                if (look.piece != Centrepiece.WORDS) Centrepiece(Phase.IDLE, 0f, Modifier.fillMaxWidth().height(170.dp))
                Text("You asked what's due this week.", color = Text2, fontSize = 13.sp)
                Spacer(Modifier.height(6.dp))
                val sp = (if (look.piece == Centrepiece.WORDS) look.size.wordsSp else look.size.sp) * 0.75f
                Text(highlighted("The project report is due Friday at 11:59 PM."), style = DisplayStyle, color = Text1,
                     fontSize = sp.sp, lineHeight = (sp * 1.15f).sp)
            }
        }

        item {
            Column {
                Label("Colour", Modifier.padding(start = 6.dp, bottom = 10.dp))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Accent.entries.forEach { a ->
                        val on = a == look.accent
                        Column(
                            Modifier.clip(RoundedCornerShape(12.dp)).clickable(role = Role.RadioButton) { set { it.copy(accent = a) } }
                                .padding(4.dp).semantics { contentDescription = "${a.label} colour" },
                            horizontalAlignment = Alignment.CenterHorizontally,
                        ) {
                            Box(
                                Modifier.size(44.dp)
                                    .border(2.dp, if (on) a.accent else Color.Transparent, CircleShape)
                                    .padding(4.dp)
                                    .background(Brush.radialGradient(listOf(Color.White, a.accent, a.deep)), CircleShape),
                            )
                            Spacer(Modifier.height(6.dp))
                            Text(a.label, color = if (on) Text1 else Text2, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }
        }

        item {
            Column {
                Label("Centrepiece", Modifier.padding(start = 6.dp, bottom = 10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Centrepiece.entries.forEach { p ->
                        val on = p == look.piece
                        Column(
                            Modifier.weight(1f).height(92.dp).clip(RoundedCornerShape(18.dp))
                                .background(if (on) Accent0.copy(alpha = 0.14f) else Color.White.copy(alpha = 0.03f))
                                .border(1.dp, if (on) Accent0 else Color.White.copy(alpha = 0.08f), RoundedCornerShape(18.dp))
                                .clickable(role = Role.RadioButton) { set { it.copy(piece = p) } },
                            horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center,
                        ) {
                            Icon(when (p) {
                                Centrepiece.REACTOR -> Icons.Filled.RadioButtonChecked
                                Centrepiece.WAVE -> Icons.Filled.GraphicEq
                                Centrepiece.WORDS -> Icons.AutoMirrored.Filled.ShortText
                            }, null, tint = if (on) Accent0 else Text2, modifier = Modifier.size(28.dp))
                            Spacer(Modifier.height(8.dp))
                            Text(p.label, color = if (on) Accent0 else Text2, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }
        }

        item {
            Column {
                Label("Font", Modifier.padding(start = 6.dp, bottom = 10.dp))
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    FontTheme.entries.chunked(2).forEach { row ->
                        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            row.forEach { f ->
                                val on = f == look.font
                                Column(
                                    Modifier.weight(1f).clip(RoundedCornerShape(16.dp))
                                        .background(if (on) Accent0.copy(alpha = 0.14f) else Color.White.copy(alpha = 0.03f))
                                        .border(1.dp, if (on) Accent0 else Color.White.copy(alpha = 0.08f), RoundedCornerShape(16.dp))
                                        .clickable(role = Role.RadioButton) { set { it.copy(font = f) } }
                                        .padding(horizontal = 14.dp, vertical = 12.dp),
                                ) {
                                    Text("Max", style = TextStyle(fontFamily = f.display, fontWeight = f.displayWeight), fontSize = 26.sp,
                                         color = if (on) Accent0 else Text1)
                                    Text(f.label, style = TextStyle(fontFamily = f.body), fontSize = 12.5.sp, color = Text2)
                                }
                            }
                            if (row.size == 1) Spacer(Modifier.weight(1f))
                        }
                    }
                }
            }
        }

        item {
            Section("Options") {
                SettingRow("Answer size", trailing = { Segmented(AnswerSize.entries, look.size, { it.label }) { s -> set { it.copy(size = s) } } })
                RowDivider()
                SettingRow("Glow", trailing = { Segmented(Glow.entries, look.glow, { it.label }) { g -> set { it.copy(glow = g) } } })
                RowDivider()
                SettingRow("Animation", subtitle = "Off keeps things still and saves a little battery",
                           trailing = { Toggle(look.motion) { m -> set { it.copy(motion = m) } } })
                if (look.piece == Centrepiece.REACTOR) {
                    RowDivider()
                    SettingRow("Voice bars around the ring", trailing = { Toggle(look.ringBars) { b -> set { it.copy(ringBars = b) } } })
                }
                if (look.piece == Centrepiece.WAVE) {
                    RowDivider()
                    SettingRow("Bar style", trailing = {
                        Segmented(listOf(false, true), look.boldBars, { if (it) "Bold" else "Thin" }) { b -> set { it.copy(boldBars = b) } }
                    })
                }
            }
        }

        item {
            Section("Reset") {
                SettingRow("Back to the default look", subtitle = "Gold · Reactor · Classic font", onClick = { set { Look() } })
            }
        }
        item { Spacer(Modifier.height(20.dp)) }
    }
}

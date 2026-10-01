package com.manaskhare.max.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.manaskhare.max.Link

// Mission-control palette, same as the dashboard
val Navy = Color(0xFF060A13)
val Panel = Color(0xFF0B1220)
val PanelHi = Color(0xFF111A2E)
val Line = Color(0x2138BDF8)
val Cyan = Color(0xFF22D3EE)
val Text1 = Color(0xFFE2E8F0)
val Text2 = Color(0xFF94A3B8)
val Dim = Color(0xFF64748B)
val Red = Color(0xFFF87171)
val Amber = Color(0xFFFBBF24)
val Green = Color(0xFF34D399)

private val colors = darkColorScheme(
    primary = Cyan, onPrimary = Navy, background = Navy, onBackground = Text1,
    surface = Panel, onSurface = Text1, surfaceVariant = PanelHi, onSurfaceVariant = Text2,
    outline = Line, error = Red, secondary = Cyan, surfaceContainer = Panel,
)

@Composable
fun MaxTheme(content: @Composable () -> Unit) = MaterialTheme(colorScheme = colors, content = content)

val Mono = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 11.sp, letterSpacing = 1.2.sp, color = Dim)

@Composable
fun Label(text: String, modifier: Modifier = Modifier, color: Color = Dim) =
    Text(text.uppercase(), style = Mono.copy(color = color), modifier = modifier)

@Composable
fun Card(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Column(
        modifier
            .fillMaxWidth()
            .background(Panel, RoundedCornerShape(14.dp))
            .border(1.dp, Line, RoundedCornerShape(14.dp))
            .padding(14.dp),
        content = content,
    )
}

@Composable
fun Title(text: String) = Text(text, color = Text1, fontSize = 22.sp, fontWeight = FontWeight.SemiBold)

@Composable
fun GhostButton(text: String, modifier: Modifier = Modifier, color: Color = Cyan, onClick: () -> Unit) =
    OutlinedButton(
        onClick = onClick, modifier = modifier, shape = RoundedCornerShape(10.dp),
        border = BorderStroke(1.dp, color.copy(alpha = 0.45f)),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = color),
    ) { Text(text, fontSize = 13.sp) }

@Composable
fun LinkChip(link: Link) {
    val (color, text) = when (link) {
        Link.ONLINE -> Green to "online"
        Link.CONNECTING -> Amber to "connecting"
        Link.UNAUTHORIZED -> Red to "not paired"
        Link.OFF -> Dim to "offline"
    }
    Row(
        Modifier.border(1.dp, color.copy(alpha = 0.4f), RoundedCornerShape(50)).padding(horizontal = 10.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(7.dp).background(color, CircleShape))
        Spacer(Modifier.width(6.dp))
        Label(text, color = color)
    }
}

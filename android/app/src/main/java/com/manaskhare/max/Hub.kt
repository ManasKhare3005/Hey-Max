package com.manaskhare.max

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONObject

enum class Link { OFF, CONNECTING, ONLINE, UNAUTHORIZED }

data class MaxEvent(val kind: String, val data: JSONObject)
data class PendingApproval(val id: Int, val prompt: String, val tool: String)

/** Live state shared by the background service (which owns the WebSocket) and the UI. */
object Hub {
    val link = MutableStateFlow(Link.OFF)
    val stage = MutableStateFlow("")
    val approvals = MutableStateFlow<List<PendingApproval>>(emptyList())
    val events = MutableSharedFlow<MaxEvent>(extraBufferCapacity = 64)

    fun addApproval(a: PendingApproval) = approvals.update { list -> list.filter { it.id != a.id } + a }
    fun removeApproval(id: Int) = approvals.update { list -> list.filter { it.id != id } }
}

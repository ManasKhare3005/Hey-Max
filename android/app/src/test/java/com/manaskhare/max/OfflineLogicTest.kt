package com.manaskhare.max

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class OfflineLogicTest {
    // Friday, October 9 2026, 3:20 PM
    private val now = LocalDateTime.of(2026, 10, 9, 15, 20)
    private fun at(text: String) = WhenParser.parse(text, now)
    private fun dt(m: Int, d: Int, h: Int, min: Int = 0, y: Int = 2026) = LocalDateTime.of(y, m, d, h, min)

    @Test fun relative() {
        assertEquals(dt(10, 9, 15, 40), at("in 20 min")!!.at)
        assertEquals(dt(10, 9, 17, 20), at("in 2 hours")!!.at)
        assertEquals(dt(10, 9, 16, 20), at("in an hour")!!.at)
        assertEquals(dt(10, 9, 15, 50), at("in half an hour")!!.at)
        assertEquals(dt(10, 11, 15, 20), at("in 2 days")!!.at)
        assertNull(at("in 2"))
    }

    @Test fun daysAndTimes() {
        assertEquals(When(dt(10, 10, 17), true), at("tomorrow 5pm"))
        assertEquals(dt(10, 10, 17, 30), at("Tomorrow at 5:30 PM")!!.at)
        assertEquals(dt(10, 9, 20), at("tonight")!!.at)
        assertEquals(dt(10, 9, 21), at("tonight at 9pm")!!.at)
        assertEquals(dt(10, 10, 9), at("tomorrow morning")!!.at)
        assertEquals(When(dt(10, 10, 9), false), at("tomorrow"))        // a day alone: 9 AM, no time given
        assertEquals(dt(10, 9, 18), at("6pm")!!.at)                     // later today
        assertEquals(dt(10, 10, 9), at("9am")!!.at)                     // passed today: tomorrow
        assertEquals(dt(10, 9, 17), at("at 5")!!.at)                    // a bare 5 is the afternoon
        assertEquals(dt(10, 10, 9), at("at 9")!!.at)
        assertEquals(dt(10, 9, 18, 45), at("18:45")!!.at)
        assertEquals(dt(10, 10, 12), at("tomorrow noon")!!.at)
    }

    @Test fun weekdaysAndDates() {
        assertEquals(dt(10, 13, 17), at("tuesday 5pm")!!.at)
        assertEquals(dt(10, 13, 17), at("tue at 5 pm")!!.at)
        assertEquals(dt(10, 12, 9), at("next monday")!!.at)
        assertEquals(dt(10, 16, 9), at("friday")!!.at)                   // today is Friday: next week's
        assertEquals(dt(10, 9, 18), at("friday 6pm")!!.at)               // ...unless the time is still ahead today
        assertEquals(dt(10, 15, 9), at("thursday")!!.at)
        assertEquals(dt(10, 12, 15), at("oct 12 3pm")!!.at)
        assertEquals(dt(10, 12, 15), at("12th october at 3pm")!!.at)
        assertEquals(dt(9, 1, 9, y = 2027), at("sept 1")!!.at)            // passed this year: next year
        assertEquals(dt(10, 12, 9), at("10/12")!!.at)
        assertEquals(dt(10, 12, 14), at("2026-10-12T14:00")!!.at)
        assertFalse(at("oct 12")!!.hasTime)
    }

    @Test fun nonsenseIsRejected() {
        assertNull(at(""))
        assertNull(at("blorp"))
        assertNull(at("sometime soon"))
        assertNull(at("friday after the party"))
    }

    private data class R(override val uid: String, override val updatedMs: Long, override val dirty: Boolean = false,
                         override val synced: Boolean = true, override val finished: Boolean = false) : Syncable

    @Test fun mergeRules() {
        assertTrue(SyncRules.wins(true, 1, false, 9))
        assertFalse(SyncRules.wins(false, 9, true, 1))
        assertTrue(SyncRules.wins(false, 5, false, 4))
        assertFalse(SyncRules.wins(false, 4, false, 4))

        val local = listOf(
            R("sent", 10, dirty = true, synced = false),          // made offline, sent
            R("edited-during", 30, dirty = true),                 // sent at 20, edited again while the request was out
            R("new-during", 5, dirty = true, synced = false),     // made while the request was out (not sent)
            R("gone", 1),                                         // the laptop doesn't list it any more
            R("same", 1),
        )
        val server = listOf(R("sent", 10), R("edited-during", 25), R("same", 2), R("from-laptop", 3))
        val plan = SyncRules.plan(local, server, mapOf("sent" to 10L, "edited-during" to 20L))
        assertEquals(listOf("sent", "same", "from-laptop"), plan.write.map { it.uid })
        assertEquals(listOf("gone"), plan.delete)
    }
}

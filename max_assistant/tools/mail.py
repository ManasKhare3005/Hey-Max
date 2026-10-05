"""Gmail tools: check, search, read, reply, send, archive, mark read.

The last list Max read out is remembered, so "read the second one", "reply to that" and
"archive it" work. Sending is risky: Max asks first (spoken yes, or a tap on the phone).
"""
from __future__ import annotations

import re

from .registry import ForLLM, ToolRegistry


def _listing(mails, heading: str, tag_accounts: bool = False) -> str:
    lines = [f"{i}. {'[' + m.account + '] ' if tag_accounts and m.account else ''}From {m.sender} — {m.subject} ({m.ago()})"
             f"{' [important]' if m.important else ''}: {m.snippet[:160]}" for i, m in enumerate(mails, 1)]
    return f"{heading}\n" + "\n".join(lines)


def register(reg: ToolRegistry):
    ctx = reg.context
    mail = getattr(ctx, "mail", None) if ctx else None
    if mail is None:
        return
    state = {"last": []}
    several = len(getattr(mail, "names", [])) > 1          # tag each email with its account

    def pick(which: str):
        """'1', 'the second one', 'the email from sarah', 'that' -> a Mail from the last list."""
        last = state["last"]
        w = (which or "").lower().strip()
        words = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": len(last) or 1}
        n = next((v for k, v in words.items() if k in w), None)
        if n is None and (m := re.search(r"\d+", w)):
            n = int(m.group())
        if n and 1 <= n <= len(last):
            return last[n - 1]
        for m in last:                                   # by sender or subject words
            if w and (any(t in m.sender.lower() for t in w.split() if len(t) > 2) or w in m.subject.lower()):
                return m
        if len(last) == 1 or w in ("", "it", "that", "this", "that one", "this one", "the email", "it please"):
            return last[0] if last else None
        return None

    def full(m):
        return mail.get(m.msgid, m.account) or m

    @reg.tool(
        "Check the user's Gmail inbox for unread email (newest first). Use for 'check my email', "
        "'any new emails', 'did I get any mail'.",
        params={"count": {"type": "integer", "description": "How many, default 5"}, "account": {"type": "string", "description": "Optional: personal, university or max (default: all of them)"}},
        required=[],
    )
    def mail_check(count: int = 5, account: str = ""):
        try:
            found = mail.search("is:unread in:inbox", max(1, min(int(count or 5), 10)), account=account or None)
        except Exception as exc:
            return f"Error: couldn't reach Gmail ({exc})."
        state["last"] = found
        if not found:
            return "No unread email in your inbox."
        return ForLLM(_listing(found, f"{len(found)} unread (newest first):", several) +
                      "\n\nSum these up in 1-3 spoken sentences: who wrote and what about, important ones first. "
                      "Don't read out email addresses.")

    @reg.tool(
        "Search the user's Gmail with Gmail search syntax, e.g. 'from:smith', 'subject:internship', "
        "'from:canvas newer_than:3d', 'is:important'. Use for 'any emails from my professor', 'find the email about ...'.",
        params={"query": {"type": "string", "description": "Gmail search query"},
                "count": {"type": "integer", "description": "How many, default 5"}, "account": {"type": "string", "description": "Optional: personal, university or max (default: all of them)"}},
        required=["query"],
    )
    def mail_search(query: str, count: int = 5, account: str = ""):
        try:
            found = mail.search(query, max(1, min(int(count or 5), 10)), account=account or None)
        except Exception as exc:
            return f"Error: couldn't search Gmail ({exc})."
        state["last"] = found
        if not found:
            return f"No emails match {query}."
        return ForLLM(_listing(found, f"Emails matching '{query}' (newest first):", several) +
                      "\n\nAnswer the user's question from these in 1-3 spoken sentences.")

    @reg.tool(
        "Read one email from the last list (e.g. 'read the second one', 'what does the email from Sarah say').",
        params={"which": {"type": "string", "description": "Number in the last list, or the sender/subject; 'it' for the only one"}},
        required=["which"],
    )
    def mail_read(which: str):
        m = pick(which)
        if m is None:
            return "Which email? Check or search your mail first, then say a number or the sender."
        m = full(m)
        body = m.body[:3000] or m.snippet
        return ForLLM(f"Email from {m.sender} <{m.address}>, subject '{m.subject}', {m.ago()}:\n{body}\n\n"
                      "Tell the user what it says in 2-4 spoken sentences (read short emails out; summarise long ones).")

    @reg.tool(
        "Reply to an email from the last list. Max asks the user to confirm before sending.",
        params={"which": {"type": "string", "description": "Number, sender or 'it'"},
                "text": {"type": "string", "description": "The reply, written as the user would send it"}},
        required=["which", "text"],
        risky=True,
        confirm="Send this reply: \"{text}\"?",
    )
    def mail_reply(which: str, text: str):
        m = pick(which)
        if m is None:
            return "Which email should I reply to? Check or search your mail first."
        m = full(m)
        subject = m.subject if m.subject.lower().startswith("re:") else f"Re: {m.subject}"
        try:
            mail.send(m.address, subject, text, reply_to=m)
        except Exception as exc:
            return f"Error: the reply didn't send ({exc})."
        return f"Sent your reply to {m.sender}."

    @reg.tool(
        "Send a new email from the user's Gmail. 'to' is an email address or the name of someone who has emailed "
        "the user before. Max asks the user to confirm before sending.",
        params={"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
                "account": {"type": "string", "description": "Send from: personal (default), university or max"}},
        required=["to", "body"],
        risky=True,
        confirm="Send an email to {to}: \"{body}\"?",
    )
    def mail_send(to: str, body: str, subject: str = "", account: str = ""):
        address = to.strip()
        if "@" not in address:
            people = mail.contacts(address)
            if not people:
                return f"I couldn't find an email address for {to}. Tell me the address."
            if len(people) > 1:
                return "Which one: " + ", ".join(f"{n} ({a})" for n, a in people[:3]) + "?"
            address = people[0][1]
        try:
            mail.send(address, subject or "(no subject)", body, account=account or None)
        except Exception as exc:
            return f"Error: the email didn't send ({exc})."
        return f"Sent your email to {to}."

    @reg.tool(
        "Archive an email from the last list (removes it from the inbox; it stays in All Mail).",
        params={"which": {"type": "string", "description": "Number, sender or 'it'"}},
        required=["which"],
        direct=True,
    )
    def mail_archive(which: str):
        m = pick(which)
        if m is None:
            return "Which email should I archive?"
        mail.archive(m.msgid, m.account)
        return f"Archived the email from {m.sender}."

    @reg.tool(
        "Mark an email from the last list as read ('mark it read', 'mark them all read').",
        params={"which": {"type": "string", "description": "Number, sender, 'it', or 'all'"}},
        required=["which"],
        direct=True,
    )
    def mail_mark_read(which: str):
        targets = state["last"] if (which or "").lower().strip() in ("all", "them", "all of them", "everything") else [pick(which)]
        targets = [t for t in targets if t is not None]
        if not targets:
            return "Which email should I mark as read?"
        for t in targets:
            mail.mark_read(t.msgid, t.account)
        return f"Marked {len(targets)} email{'s' if len(targets) > 1 else ''} as read."

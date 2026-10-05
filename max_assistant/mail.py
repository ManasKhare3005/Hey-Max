"""Gmail over IMAP/SMTP with an app password: check, search, read, reply, send, archive.

Mail goes from Google straight to this laptop and is read by the local model; nothing is sent
anywhere else. Sending always needs the user's yes (the tools are marked risky).
Gmail's IMAP extensions are used for its own search syntax (X-GM-RAW), stable message ids
(X-GM-MSGID), threads (X-GM-THRID) and labels (archive = remove the Inbox label).
"""
from __future__ import annotations

import datetime as dt
import email
import email.utils
import imaplib
import logging
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage

log = logging.getLogger(__name__)
IMAP_HOST, SMTP_HOST = "imap.gmail.com", "smtp.gmail.com"
ALL_MAIL = '"[Gmail]/All Mail"'


@dataclass
class Mail:
    msgid: str              # X-GM-MSGID: stable across folders
    thread: str             # X-GM-THRID (hex in Gmail web links)
    sender: str             # display name, or the address
    address: str
    subject: str
    date: dt.datetime | None
    snippet: str
    unread: bool = False
    important: bool = False
    message_id: str = ""    # RFC Message-ID header, for replies
    body: str = ""
    account: str = ""       # which mailbox (personal / university / max)

    @property
    def link(self) -> str:
        return f"https://mail.google.com/mail/u/0/#all/{int(self.thread):x}" if self.thread.isdigit() else ""

    def ago(self, now: dt.datetime | None = None) -> str:
        if not self.date:
            return ""
        now = now or dt.datetime.now(self.date.tzinfo)
        mins = int((now - self.date).total_seconds() // 60)
        if mins < 60:
            return f"{max(mins, 1)} min ago"
        if mins < 24 * 60:
            return f"{mins // 60} h ago"
        return f"{self.date:%b} {self.date.day}"


def _text(value) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    import html as h

    text = h.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def body_text(msg: email.message.Message) -> str:
    """Plain text of a message (prefers text/plain, else stripped HTML), quoted replies dropped."""
    plain, html = [], []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        (plain if part.get_content_type() == "text/plain" else html if part.get_content_type() == "text/html" else []).append(text)
    text = "\n".join(plain) if plain else html_to_text("\n".join(html))
    # Drop the quoted earlier message ("On Mon, ... wrote:" and "> ..." lines)
    text = re.split(r"\n\s*On .{5,120} wrote:\s*\n", text)[0]
    lines = [l for l in text.splitlines() if not l.lstrip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse(raw: bytes, msgid: str = "", thread: str = "", flags: str = "", labels: str = "") -> Mail:
    msg = email.message_from_bytes(raw)
    name, addr = email.utils.parseaddr(_text(msg.get("From")))
    try:
        date = email.utils.parsedate_to_datetime(msg.get("Date"))
    except Exception:
        date = None
    body = body_text(msg)
    return Mail(msgid, thread, name or addr, addr, _text(msg.get("Subject")) or "(no subject)", date,
                re.sub(r"\s+", " ", body)[:300], unread="\\Seen" not in flags, important="\\Important" in labels,
                message_id=msg.get("Message-ID", ""), body=body)


class GmailClient:
    def __init__(self, address: str, app_password: str, timeout: float = 20):
        self.address = address
        self.password = app_password.replace(" ", "")
        self.timeout = timeout

    def _imap(self, folder: str = "INBOX", readonly: bool = True) -> imaplib.IMAP4_SSL:
        m = imaplib.IMAP4_SSL(IMAP_HOST, timeout=self.timeout)
        m.login(self.address, self.password)
        m.select(folder, readonly=readonly)
        return m

    def _fetch(self, m, uids: list[bytes], full: bool = False) -> list[Mail]:
        out = []
        part = "BODY.PEEK[]" if full else "BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.4000>"
        for uid in uids:
            typ, data = m.uid("FETCH", uid, f"(X-GM-MSGID X-GM-THRID X-GM-LABELS FLAGS {part})")
            if typ != "OK" or not data or not isinstance(data[0], tuple):
                continue
            meta = data[0][0].decode(errors="replace")
            raw = b"".join(d[1] for d in data if isinstance(d, tuple))
            msgid = re.search(r"X-GM-MSGID (\d+)", meta)
            thread = re.search(r"X-GM-THRID (\d+)", meta)
            labels = re.search(r"X-GM-LABELS \((.*?)\)", meta)
            flags = re.search(r"FLAGS \((.*?)\)", meta)
            out.append(parse(raw, msgid.group(1) if msgid else "", thread.group(1) if thread else "",
                             flags.group(1) if flags else "", labels.group(1) if labels else ""))
        return out

    def search(self, query: str = "is:unread in:inbox", limit: int = 5) -> list[Mail]:
        """Gmail search syntax: 'from:smith', 'subject:internship newer_than:7d', 'is:important is:unread'."""
        m = self._imap(ALL_MAIL)
        try:
            typ, data = m.uid("SEARCH", "X-GM-RAW", f'"{query}"')
            uids = (data[0].split() if typ == "OK" and data and data[0] else [])[-limit:][::-1]   # newest first
            return self._fetch(m, uids)
        finally:
            m.logout()

    def get(self, msgid: str) -> Mail | None:
        m = self._imap(ALL_MAIL)
        try:
            typ, data = m.uid("SEARCH", "X-GM-MSGID", msgid)
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            found = self._fetch(m, uids[:1], full=True)
            return found[0] if found else None
        finally:
            m.logout()

    def _store(self, msgid: str, *command: str):
        m = self._imap(ALL_MAIL, readonly=False)
        try:
            typ, data = m.uid("SEARCH", "X-GM-MSGID", msgid)
            for uid in (data[0].split() if typ == "OK" and data and data[0] else []):
                m.uid("STORE", uid, *command)
        finally:
            m.logout()

    def mark_read(self, msgid: str):
        self._store(msgid, "+FLAGS", "(\\Seen)")

    def archive(self, msgid: str):
        self._store(msgid, "-X-GM-LABELS", "(\\Inbox)")

    def send(self, to: str, subject: str, body: str, reply_to: Mail | None = None):
        msg = EmailMessage()
        msg["From"], msg["To"] = self.address, to
        msg["Subject"] = subject
        if reply_to is not None and reply_to.message_id:
            msg["In-Reply-To"] = msg["References"] = reply_to.message_id   # keeps it in the same thread
        msg.set_content(body)
        with smtplib.SMTP_SSL(SMTP_HOST, 465, context=ssl.create_default_context(), timeout=self.timeout) as s:
            s.login(self.address, self.password)
            s.send_message(msg)

    def contacts(self, name: str, limit: int = 40) -> list[tuple[str, str]]:
        """People matching `name` among those who emailed the user (last 2 years): [(name, address)]."""
        seen: dict[str, str] = {}
        for mail in self.search(f"from:({name}) newer_than:2y", limit):
            if mail.address and mail.address.lower() != self.address.lower():
                seen.setdefault(mail.address.lower(), mail.sender)
        return [(n, a) for a, n in seen.items()]


class MailAccounts:
    """Several Gmail accounts behind the GmailClient interface. Searches cover every account
    unless one is named; each Mail remembers its account, so replies go out from it."""

    def __init__(self, clients: dict[str, GmailClient], default: str = "personal"):
        self.clients = clients
        self.default = default if default in clients else next(iter(clients))

    @property
    def names(self) -> list[str]:
        return list(self.clients)

    def resolve(self, account: str | None) -> list[str]:
        a = (account or "").lower().strip()
        if not a or a in ("all", "every", "any", "everything"):
            return self.names
        for name in self.names:                    # "uni", "school", "asu" -> university; "max's" -> max
            if name.startswith(a[:3]) or a.startswith(name[:3]) or (name == "university" and a in ("school", "asu", "college")):
                return [name]
        return self.names

    def search(self, query: str = "is:unread in:inbox", limit: int = 5, account: str | None = None) -> list[Mail]:
        found: list[Mail] = []
        errors = []
        for name in self.resolve(account):
            try:
                for m in self.clients[name].search(query, limit):
                    m.account = name
                    found.append(m)
            except Exception as exc:                 # one bad password shouldn't hide the other inboxes
                errors.append(f"{name}: {exc}")
        if errors and not found:
            raise RuntimeError("; ".join(errors))
        found.sort(key=lambda m: m.date.timestamp() if m.date else 0, reverse=True)
        return found[:limit]

    def _client(self, account: str | None) -> GmailClient:
        return self.clients.get(account or "", self.clients[self.default])

    def get(self, msgid: str, account: str | None = None) -> Mail | None:
        m = self._client(account).get(msgid)
        if m is not None:
            m.account = account or self.default
        return m

    def send(self, to: str, subject: str, body: str, reply_to: Mail | None = None, account: str | None = None):
        name = (reply_to.account if reply_to is not None and reply_to.account else None) or (self.resolve(account)[0] if account else self.default)
        self._client(name).send(to, subject, body, reply_to=reply_to)

    def archive(self, msgid: str, account: str | None = None):
        self._client(account).archive(msgid)

    def mark_read(self, msgid: str, account: str | None = None):
        self._client(account).mark_read(msgid)

    def contacts(self, name: str, limit: int = 40) -> list[tuple[str, str]]:
        seen: dict[str, str] = {}
        for client in self.clients.values():
            try:
                for n, a in client.contacts(name, limit):
                    seen.setdefault(a.lower(), n)
            except Exception:
                continue
        return [(n, a) for a, n in seen.items()]


def accounts_from_secrets(gmail: dict) -> MailAccounts | None:
    """secrets.yaml gmail: either {address, app_password} or {personal: {...}, university: {...}, ...}.
    Slots still holding the placeholders are skipped."""
    def usable(slot) -> bool:
        return isinstance(slot, dict) and "@" in str(slot.get("address", "")) and slot.get("app_password") \
            and "xxxx" not in str(slot.get("app_password")) and not str(slot.get("address", "")).startswith(("you@", "max@gmail"))
    if "address" in (gmail or {}):
        slots = {"personal": gmail}
    else:
        slots = {name: slot for name, slot in (gmail or {}).items()}
    clients = {name: GmailClient(str(s["address"]), str(s["app_password"])) for name, s in slots.items() if usable(s)}
    return MailAccounts(clients) if clients else None

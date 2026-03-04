import os
import json
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

TZ = ZoneInfo("Europe/Paris")

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
CHAT_ID = os.environ["TG_CHAT_ID"]

# Sources FF XML (fallback automatique)
FF_XML_URLS = [
    "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
    "https://cdn-nfs.faireconomy.media/ff_calendar_thisweek.xml",
]

STATE_FILE = Path("state.json")

# Paramètres
ALLOWED_CURRENCIES = {"USD", "EUR", "GBP"}
ALLOWED_IMPACT = {"Medium", "High"}
REMINDER_LEAD_MIN = 1
REMINDER_WINDOW_MIN = 6          # tolérance, car GitHub Actions tourne toutes les 5 min
SOURCE_FAIL_ALERT_AFTER = 3      # nb d'échecs consécutifs avant alerte Telegram


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "sent_daily": {},          # YYYY-MM-DD -> iso
        "sent_reminders": {},      # key -> iso
        "source_failures": 0,      # compteur échecs consécutifs
        "last_source_alert": None  # iso date-time
    }


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def tg_send(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    }, timeout=20)
    r.raise_for_status()


def fetch_ff_xml() -> str:
    last_err = None
    headers = {"User-Agent": "macro-alerts-telegram/1.0"}
    for url in FF_XML_URLS:
        try:
            r = requests.get(url, headers=headers, timeout=25)
            r.raise_for_status()
            if "<weeklyevents" in r.text or "<event" in r.text:
                return r.text
            last_err = RuntimeError(f"Unexpected content from {url}")
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"All FF XML URLs failed. Last error: {last_err}")


def parse_ff_datetime(date_str: str, time_str: str) -> datetime | None:
    """
    FF XML typique:
      date: "03-04-2026" (MM-DD-YYYY)
      time: "10:00am" / "3:30pm"
    On ignore All Day / Tentative / vide.
    IMPORTANT: FF ne précise pas toujours explicitement la TZ dans ce flux.
    Ici on traite l'heure comme "heure affichable" et on l'ancre en Europe/Paris
    (ce qui est généralement ce que tu veux pour tes rappels).
    """
    if not date_str or not time_str:
        return None

    date_str = date_str.strip()
    time_str = time_str.strip()

    if time_str.lower() in ("all day", "day", "tentative"):
        return None

    try:
        d = datetime.strptime(date_str, "%m-%d-%Y").date()
    except ValueError:
        return None

    try:
        t = datetime.strptime(time_str.lower(), "%I:%M%p").time()
    except ValueError:
        return None

    return datetime.combine(d, t).replace(tzinfo=TZ)


def fetch_events() -> list[tuple[datetime, dict]]:
    xml_text = fetch_ff_xml()
    root = ET.fromstring(xml_text)

    events = []
    for ev in root.findall(".//event"):
        title = (ev.findtext("title") or "").strip()
        country = (ev.findtext("country") or "").strip().upper()    # USD/EUR/GBP
        impact = (ev.findtext("impact") or "").strip()              # Low/Medium/High
        date_s = (ev.findtext("date") or "").strip()
        time_s = (ev.findtext("time") or "").strip()

        if country not in ALLOWED_CURRENCIES:
            continue
        if impact not in ALLOWED_IMPACT:
            continue

        dt = parse_ff_datetime(date_s, time_s)
        if dt is None:
            continue

        events.append((dt, {"title": title, "country": country, "impact": impact}))

    events.sort(key=lambda x: x[0])
    return events


def fmt_line(dt: datetime, ev: dict) -> str:
    return f"{dt.strftime('%H:%M')} — [{ev['impact']}] {ev['country']} — {ev['title']}"


def main():
    state = load_state()
    now = datetime.now(TZ)
    today_key = now.date().isoformat()

    # 1) Récupération events avec fallback + monitoring
    try:
        events = fetch_events()
        # reset failures si OK
        state["source_failures"] = 0
    except Exception as e:
        state["source_failures"] = int(state.get("source_failures", 0)) + 1

            # DEBUG: sur exécution manuelle, envoyer les prochains events
    if os.environ.get("DEBUG_NEXT") == "1":
        upcoming = []
        for dt, ev in events:
            if dt >= now:
                upcoming.append(f"{dt.strftime('%a %H:%M')} — [{ev['impact']}] {ev['country']} — {ev['title']}")
            if len(upcoming) >= 3:
                break
        if not upcoming:
            tg_send("DEBUG: aucun event à venir trouvé dans le XML.")
        else:
            tg_send("DEBUG: prochains events (heure telle que dans le feed)\n" + "\n".join(upcoming))

        # Alerte source cassée (pas à chaque run)
        if state["source_failures"] >= SOURCE_FAIL_ALERT_AFTER:
            # évite spam: max 1 alerte toutes les 6h
            last = state.get("last_source_alert")
            allow = True
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    if (now - last_dt.replace(tzinfo=TZ)) < timedelta(hours=6):
                        allow = False
                except Exception:
                    pass

            if allow:
                tg_send(
                    "⚠️ Calendrier macro indisponible.\n"
                    "Les URLs ForexFactory XML semblent avoir changé ou être bloquées.\n"
                    f"Détail: {type(e).__name__}: {e}"
                )
                state["last_source_alert"] = now.isoformat()

        save_state(state)
        return

    # 2) Résumé quotidien à 07:00 Paris (une seule fois)
    if today_key not in state["sent_daily"] and (now.hour == 7 and now.minute <= 5):
        lines = [fmt_line(dt, ev) for dt, ev in events if dt.date() == now.date()]
        if lines:
            msg = "🗓️ Macro du jour (USD/EUR/GBP — Medium+High)\n" + "\n".join(lines)
        else:
            msg = "🗓️ Macro du jour\nAucun événement Medium/High (USD/EUR/GBP) aujourd’hui."
        tg_send(msg)
        state["sent_daily"][today_key] = now.isoformat()

    # 3) Rappels T-15 (fenêtre tolérance)
    start = now + timedelta(minutes=REMINDER_LEAD_MIN)
    end = start + timedelta(minutes=REMINDER_WINDOW_MIN)

    for dt, ev in events:
        if not (start <= dt < end):
            continue

        key = f"{dt.isoformat()}::{ev['country']}::{ev['impact']}::{ev['title']}"
        if key in state["sent_reminders"]:
            continue

        msg = (
            f"⏰ Dans 15 min — [{ev['impact']}] {ev['country']}\n"
            f"{ev['title']}\n"
            f"Heure: {dt.strftime('%H:%M')} (Paris)"
        )
        tg_send(msg)
        state["sent_reminders"][key] = now.isoformat()

    save_state(state)


if __name__ == "__main__":
    main()

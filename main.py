import os
import json
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

    # toujours autoriser HIGH
    if impact == "High":
        return True

    # MEDIUM seulement pour USD
    if impact == "Medium" and currency == "USD":
        return True

    return False
    

def is_allowed_event(ev):
    impact = ev["impact"]
    currency = ev["country"]

    if impact == "High":
        return True

    if impact == "Medium" and currency == "USD":
        return True

    return False
    

# ✅ TES ACTIFS SUIVIS (filtrage final)
WATCHED_ASSETS = {"EURUSD", "GBPUSD", "XAUUSD", "DE30"}

REMINDER_LEAD_MIN = 15
REMINDER_WINDOW_MIN = 6          # tolérance, conservé pour compatibilité
SOURCE_FAIL_ALERT_AFTER = 3      # nb d'échecs consécutifs avant alerte Telegram


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "sent_daily": {},
        "sent_reminders": {},
        "source_failures": 0,
        "last_source_alert": None
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

    dt_utc = datetime.combine(d, t).replace(tzinfo=ZoneInfo("UTC"))
    return dt_utc.astimezone(TZ)


def fetch_events() -> list[tuple[datetime, dict]]:
    xml_text = fetch_ff_xml()
    root = ET.fromstring(xml_text)

    events = []
    for ev in root.findall(".//event"):
        title = (ev.findtext("title") or "").strip()
        country = (ev.findtext("country") or "").strip().upper()
        impact = (ev.findtext("impact") or "").strip()
        date_s = (ev.findtext("date") or "").strip()
        time_s = (ev.findtext("time") or "").strip()

        if country not in ALLOWED_CURRENCIES:
            continue
        if not is_allowed_event({"impact": impact, "country": country}):
            continue
              

        dt = parse_ff_datetime(date_s, time_s)
        if dt is None:
            continue

        events.append((dt, {"title": title, "country": country, "impact": impact}))

    events.sort(key=lambda x: x[0])
    return events


def flag_for_currency(cur: str) -> str:
    return {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧"}.get(cur, "🏳️")


def impacted_assets(currency: str) -> list[str]:
    """
    Mapping "trading" (simple et utile).
    - USD impacte: EURUSD, GBPUSD, XAUUSD
    - EUR impacte: EURUSD + DE30
    - GBP impacte: GBPUSD
    """
    c = currency.upper()
    if c == "USD":
        return ["EURUSD", "GBPUSD", "XAUUSD"]
    if c == "EUR":
        return ["EURUSD", "DE30"]
    if c == "GBP":
        return ["GBPUSD"]
    return []


def relevant_assets_for_event(ev: dict) -> list[str]:
    assets = impacted_assets(ev["country"])
    return [a for a in assets if a in WATCHED_ASSETS]


def is_relevant_event(ev: dict) -> bool:
    return len(relevant_assets_for_event(ev)) > 0


def format_macro_alert(dt_local: datetime, ev: dict, lead_min: int) -> str:
    cur = ev["country"]
    imp = ev["impact"].upper()
    title = ev["title"]

    # Couleur selon impact
    if imp == "HIGH":
        impact_icon = "🔴 HIGH IMPACT"
    else:
        impact_icon = "🟠 MEDIUM IMPACT"

    assets = relevant_assets_for_event(ev)
    assets_block = "\n".join(f"• {a}" for a in assets) if assets else "• (aucun)"

    return (
        f"{impact_icon}\n\n"
        f"⏰ Dans {lead_min} min — {dt_local.strftime('%H:%M')} (Paris)\n\n"
        f"{flag_for_currency(cur)} {cur}\n"
        f"{title}\n\n"
        "Actifs concernés\n"
        f"{assets_block}"
    )
    

def format_daily_summary(day: datetime.date, events: list[tuple[datetime, dict]]) -> str:
    lines_by_cur = {"USD": [], "EUR": [], "GBP": []}

    for dt, ev in events:
        if dt.date() != day:
            continue
        if not is_relevant_event(ev):
            continue

        cur = ev["country"]
        assets = ", ".join(relevant_assets_for_event(ev))
        lines_by_cur[cur].append(f"{dt.strftime('%H:%M')} — [{ev['impact']}] {ev['title']}  ({assets})")

    parts = ["🗓️ Macro du jour (Medium+High) — filtré sur tes actifs"]

    for cur in ("EUR", "GBP", "USD"):
        if lines_by_cur[cur]:
            parts.append(f"\n{flag_for_currency(cur)} {cur}")
            parts.extend(lines_by_cur[cur])

    if len(parts) == 1:
        return (
            "🗓️ Macro du jour\n"
            "Aucun événement Medium/High pertinent aujourd’hui (sur EURUSD/GBPUSD/XAUUSD/DE30)."
        )

    return "\n".join(parts)


def main():
    state = load_state()
    now = datetime.now(TZ)
    today_key = now.date().isoformat()

    # 1) Récupération events avec fallback + monitoring
    try:
        events = fetch_events()
        state["source_failures"] = 0
    except Exception as e:
        state["source_failures"] = int(state.get("source_failures", 0)) + 1

        if state["source_failures"] >= SOURCE_FAIL_ALERT_AFTER:
            last = state.get("last_source_alert")
            allow = True
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=TZ)
                    if (now - last_dt) < timedelta(hours=6):
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

    # DEBUG : seulement si le fetch a réussi
    if os.environ.get("DEBUG_NEXT") == "1":
        upcoming = []
        for dt, ev in events:
            if dt >= now:
                upcoming.append(f"{dt.strftime('%a %H:%M')} — [{ev['impact']}] {ev['country']} — {ev['title']}")
            if len(upcoming) >= 5:
                break

        tg_send(
            "DEBUG CALENDAR\n\n"
            f"Now: {now.strftime('%a %H:%M')} (Paris)\n\n"
            "Prochains événements:\n"
            + ("\n".join(upcoming) if upcoming else "(aucun)")
        )

    # 2) Résumé quotidien à 07:00 Paris (une seule fois)
    if today_key not in state["sent_daily"] and (now.hour == 7 and now.minute <= 5):
        msg = format_daily_summary(now.date(), events)
        tg_send(msg)
        state["sent_daily"][today_key] = now.isoformat()

    # 3) Rappels T-15 robustes (anti-miss)
    for dt, ev in events:
        if not is_relevant_event(ev):
            continue

        reminder_time = dt - timedelta(minutes=REMINDER_LEAD_MIN)

        # envoyer si on est après T-15 mais avant la news
        if not (reminder_time <= now < dt):
            continue

        key = f"{dt.isoformat()}::{ev['country']}::{ev['impact']}::{ev['title']}"
        if key in state["sent_reminders"]:
            continue

        msg = format_macro_alert(dt, ev, REMINDER_LEAD_MIN)
        tg_send(msg)
        state["sent_reminders"][key] = now.isoformat()

    save_state(state)


if __name__ == "__main__":
    main()

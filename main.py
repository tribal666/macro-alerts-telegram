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
REMINDER_WINDOW_MIN = 6  # tolérance, conservé pour compatibilité
SOURCE_FAIL_ALERT_AFTER = 3  # nb d'échecs consécutifs avant alerte Telegram

CRITICAL_KEYWORDS = ["speaks", "speech", "press conference", "testifies", "hearing"]

MACRO_EXPLAIN = {
    "CPI": "Indice des prix à la consommation. Mesure l'inflation.",
    "Core CPI": "Inflation hors alimentation et énergie.",
    "PPI": "Indice des prix à la production.",
    "GDP": "Produit intérieur brut, mesure la croissance économique.",
    "Retail Sales": "Mesure les ventes au détail, indicateur clé de la consommation.",
    "Non-Farm Payrolls": "Variation mensuelle de l'emploi aux États-Unis.",
    "Unemployment": "Taux de chômage.",
    "Interest Rate": "Décision de politique monétaire.",
    "FOMC": "Décision de politique monétaire de la Réserve fédérale.",
}


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {
        "sent_reminders": {},
        "sent_daily": {},
        "seen_events": [],
        "sent_releases": {},
        "source_failures": 0,
        "last_source_alert": None,
    }


def is_critical_event(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in CRITICAL_KEYWORDS)


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def tg_send(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
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
        forecast = (ev.findtext("forecast") or "").strip()
        previous = (ev.findtext("previous") or "").strip()
        actual = (ev.findtext("actual") or "").strip()

        if country not in ALLOWED_CURRENCIES:
            continue
        if not is_allowed_event({"impact": impact, "country": country}):
            continue

        dt = parse_ff_datetime(date_s, time_s)
        if dt is None:
            continue

        events.append(
            (
                dt,
                {
                    "title": title,
                    "country": country,
                    "impact": impact,
                    "forecast": forecast,
                    "previous": previous,
                    "actual": actual,
                },
            )
        )

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


def format_macro_alert(dt_local: datetime, ev: dict, minutes_left: int) -> str:
    cur = ev["country"]
    imp = ev["impact"].upper()
    title = ev["title"]

    forecast = ev.get("forecast")
    previous = ev.get("previous")

    explain = ""
    for key in MACRO_EXPLAIN:
        if key.lower() in title.lower():
            explain = MACRO_EXPLAIN[key]
            break

    values = ""
    if forecast:
        values += f"\n📊 Prévision : {forecast}"
    if previous:
        values += f"\n📊 Précédent : {previous}"

    if imp == "HIGH":
        if is_critical_event(title):
            impact_icon = "🔥🔥🔥 DISCOURS MAJEUR 🔥🔥🔥"
        else:
            impact_icon = "🚨🚨🚨 HIGH IMPACT 🚨🚨🚨"
    else:
        impact_icon = "🟠 MEDIUM IMPACT"

    assets = relevant_assets_for_event(ev)
    assets_block = "\n".join(f"• {a}" for a in assets) if assets else "• (aucun)"

    return (
        f"{impact_icon}\n\n"
        f"⏰ Dans {minutes_left} min — {dt_local.strftime('%H:%M')} (Paris)\n\n"
        f"{flag_for_currency(cur)} {cur}\n"
        f"{title}\n\n"
        f"{explain}"
        f"{values}\n\n"
        "Actifs concernés\n"
        f"{assets_block}"
    )


def parse_ff_number(value: str | None):
    if not value:
        return None

    try:
        v = value.strip()

        multiplier = 1

        if v.endswith("%"):
            v = v[:-1]

        if v.endswith("K"):
            multiplier = 1_000
            v = v[:-1]

        if v.endswith("M"):
            multiplier = 1_000_000
            v = v[:-1]

        if v.endswith("B"):
            multiplier = 1_000_000_000
            v = v[:-1]

        return float(v) * multiplier

    except Exception:
        return None
    
def compute_surprise(actual, forecast):
    a = parse_ff_number(actual)
    f = parse_ff_number(forecast)

    if a is None or f is None:
        return None

    try:
        if f == 0:
            return None
        return (a - f) / abs(f)
    except Exception:
        return None    

def format_release_alert(dt_local: datetime, ev: dict) -> str:
    cur = ev["country"]
    title = ev["title"]

    actual = ev.get("actual")
    forecast = ev.get("forecast")
    previous = ev.get("previous")

    surprise = compute_surprise(actual, forecast)

    surprise_text = ""
    impact_text = ""

    if surprise is not None:
        surprise_text = f"\n⚡ Surprise : {surprise*100:+.2f}%"

        if surprise > 0:
            impact_text = f"📈 Impact probable : {cur} bullish"
        elif surprise < 0:
            impact_text = f"📉 Impact probable : {cur} bearish"
        else:
            impact_text = "➖ Impact probable : neutre"

    return (
        "🚨 DONNÉE MACRO PUBLIÉE\n\n"
        f"{flag_for_currency(cur)} {cur}\n"
        f"{title}\n\n"
        f"Actual : {actual}\n"
        f"Forecast : {forecast}\n"
        f"Previous : {previous}"
        f"{surprise_text}\n\n"
        f"{impact_text}\n\n"
        f"🕒 {dt_local.strftime('%H:%M')} (Paris)"
    )


def format_daily_summary(
    day: datetime.date, events: list[tuple[datetime, dict]]
) -> str:
    header = f"🗓️ Macro de demain — {day.strftime('%d/%m/%Y')}\n\n"

    high_events = []
    other_events = []

    for dt_local, ev in sorted(events, key=lambda x: x[0]):
        if dt_local.date() != day:
            continue

        cur = ev["country"]
        imp = ev["impact"].upper()
        title = ev["title"]

        assets = relevant_assets_for_event(ev)
        assets_str = ", ".join(assets) if assets else "-"

        line = f"{dt_local.strftime('%H:%M')} {cur} {title} ({assets_str})"

        if imp == "HIGH":
            high_events.append(line)
        else:
            other_events.append(line)

    msg = header

    if high_events:
        msg += "🚨 HIGH IMPACT\n"
        msg += "\n".join(high_events)
        msg += "\n\n"

    if other_events:
        msg += "📊 AUTRES ANNONCES\n"
        msg += "\n".join(other_events)

    if not high_events and not other_events:
        msg += "Aucune annonce pertinente."

    return msg


def main():
    state = load_state()

    # sécurité structure state.json
    state.setdefault("sent_releases", {})
    state.setdefault("sent_reminders", {})
    state.setdefault("sent_daily", {})
    state.setdefault("seen_events", [])

    now = datetime.now(TZ)

    # 1) Récupération events avec fallback + monitoring
    try:
        events = fetch_events()

        # Détection nouvelles annonces
        seen = set(state.get("seen_events", []))
        for dt, ev in events:
            key = f"{dt.isoformat()}_{ev['country']}_{ev['title']}"

            # ne traiter que les events entre -10 min et +12h
            if not (-600 <= (dt - now).total_seconds() <= 43200):
                continue

            if key not in seen:
                msg = (
                    "⚡ NOUVELLE NEWS MACRO\n\n"
                    f"{flag_for_currency(ev['country'])} {ev['country']}\n"
                    f"{ev['title']}\n\n"
                    f"📅 {dt.strftime('%d/%m')}\n"
                    f"🕒 {dt.strftime('%H:%M')} (Paris)"
                )
                tg_send(msg)
                seen.add(key)

        state["seen_events"] = list(seen)[-200:]
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

    # 2) Résumé à 22:00 Paris pour le lendemain
    tomorrow = (now + timedelta(days=1)).date()
    tomorrow_key = tomorrow.isoformat()

    if tomorrow_key not in state["sent_daily"] and (
        now.hour == 22 and now.minute <= 15
    ):
        tomorrow_events = [(dt, ev) for dt, ev in events if dt.date() == tomorrow]
        msg = format_daily_summary(tomorrow, tomorrow_events)
        tg_send(msg)
        state["sent_daily"][tomorrow_key] = now.isoformat()

    # 3) Rappels T-15 robustes (anti-miss)
    for dt, ev in events:
        if not is_relevant_event(ev):
            continue

        reminder_time = dt - timedelta(minutes=REMINDER_LEAD_MIN)
        key = f"{dt.isoformat()}::{ev['country']}::{ev['impact']}::{ev['title']}"

        # ----- REMINDER -----
        if reminder_time <= now < dt:
            if key not in state["sent_reminders"]:
                minutes_left = max(0, int((dt - now).total_seconds() / 60))
                msg = format_macro_alert(dt, ev, minutes_left)
                tg_send(msg)
                state["sent_reminders"][key] = now.isoformat()

        # ----- RELEASE -----
        actual = ev.get("actual")

        # clé unique de la news
        key_release = f"{dt.isoformat()}::{ev['country']}::{ev['title']}"

        # créer la structure si elle n'existe pas
        state.setdefault("sent_releases", {})

        # si déjà envoyée → ne rien faire
        if key_release in state["sent_releases"]:
            continue

        # si la donnée existe → envoyer
        if actual and actual != "-":
            msg = format_release_alert(dt, ev)
            tg_send(msg)

            # mémoriser immédiatement
            state["sent_releases"][key_release] = now.isoformat()

            save_state(state)

            continue


if __name__ == "__main__":
    main()

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

STATE_FILE = Path.cwd() / "state.json"

# Paramètres
ALLOWED_CURRENCIES = {"USD", "EUR", "GBP"}


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

MACRO_DIRECTION = {
    # inflation
    "CPI": -1,
    "Core CPI": -1,
    "PPI": -1,
    "Core PPI": -1,
    # croissance
    "GDP": 1,
    "Retail Sales": 1,
    # emploi
    "Non-Farm": 1,
    "Unemployment": -1,
    "Jobless": -1,
    # sentiment
    "Consumer Sentiment": 1,
}

import re

FLAGS = {
    "USD": "🇺🇸",
    "EUR": "🇪🇺",
    "GBP": "🇬🇧",
}

EXACT_TRANSLATIONS = {
    "fomc statement": "Communiqué du FOMC",
    "fomc economic projections": "Projections économiques du FOMC",
    "federal funds rate": "Décision de taux de la Fed",
    "interest rate decision": "Décision de taux",
    "main refinancing rate": "Taux de refinancement principal",
    "monetary policy statement": "Communiqué de politique monétaire",
    "non-farm payrolls": "Créations d’emplois non agricoles (NFP)",
    "adp non-farm employment change": "Emplois privés ADP",
    "unemployment rate": "Taux de chômage",
    "claimant count change": "Variation des demandeurs d’emploi",
    "average hourly earnings m/m": "Salaire horaire moyen (mensuel)",
    "average earnings index 3m/y": "Salaire moyen sur 3 mois (annuel)",
    "retail sales m/m": "Ventes au détail (mensuelles)",
    "core retail sales m/m": "Ventes au détail hors éléments volatils (mensuelles)",
    "gdp q/q": "PIB trimestriel",
    "gdp m/m": "PIB mensuel",
    "final gdp q/q": "PIB final trimestriel",
    "prelim gdp q/q": "PIB préliminaire trimestriel",
    "flash gdp q/q": "PIB flash trimestriel",
    "trade balance": "Balance commerciale",
    "current account": "Balance des paiements courants",
    "cpi m/m": "Inflation CPI (mensuelle)",
    "cpi y/y": "Inflation CPI (annuelle)",
    "core cpi m/m": "Inflation sous-jacente CPI (mensuelle)",
    "core cpi y/y": "Inflation sous-jacente CPI (annuelle)",
    "trimmed mean cpi y/y": "Inflation CPI moyenne tronquée (annuelle)",
    "ppi m/m": "Inflation à la production PPI (mensuelle)",
    "ppi y/y": "Inflation à la production PPI (annuelle)",
    "core ppi m/m": "Inflation sous-jacente PPI (mensuelle)",
    "core ppi y/y": "Inflation sous-jacente PPI (annuelle)",
    "pce price index m/m": "Indice PCE (mensuel)",
    "pce price index y/y": "Indice PCE (annuel)",
    "core pce price index m/m": "Indice PCE sous-jacent (mensuel)",
    "core pce price index y/y": "Indice PCE sous-jacent (annuel)",
    "ism manufacturing pmi": "ISM manufacturier",
    "ism services pmi": "ISM services",
    "manufacturing pmi": "PMI manufacturier",
    "services pmi": "PMI services",
    "flash manufacturing pmi": "PMI manufacturier flash",
    "flash services pmi": "PMI services flash",
    "consumer confidence": "Confiance des consommateurs",
    "cb consumer confidence": "Confiance des consommateurs Conference Board",
    "final consumer sentiment": "Sentiment final des consommateurs",
    "prelim uom consumer sentiment": "Sentiment préliminaire des consommateurs (Université du Michigan)",
    "building permits": "Permis de construire",
    "housing starts": "Mises en chantier",
    "existing home sales": "Ventes de logements existants",
    "new home sales": "Ventes de logements neufs",
    "pending home sales m/m": "Promesses de ventes immobilières (mensuelles)",
    "durable goods orders m/m": "Commandes de biens durables (mensuelles)",
    "core durable goods orders m/m": "Commandes de biens durables hors transport (mensuelles)",
    "crude oil inventories": "Stocks hebdomadaires de pétrole brut",
    "10-y bond auction": "Adjudication d’obligations à 10 ans",
    "30-y bond auction": "Adjudication d’obligations à 30 ans",
}


def normalize_event_title(title: str) -> str:
    s = (title or "").strip().lower()
    s = s.replace("&amp;", "&")
    s = s.replace("'", "")
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def smart_translate_event(title: str) -> str:
    key = normalize_event_title(title)

    if key in EXACT_TRANSLATIONS:
        return EXACT_TRANSLATIONS[key]

    if "fomc" in key and "statement" in key:
        return "Communiqué du FOMC"
    if "fomc" in key and "projection" in key:
        return "Projections économiques du FOMC"
    if "interest rate" in key or "rate decision" in key:
        return "Décision de taux"
    if "non farm payroll" in key or key == "nfp":
        return "Créations d’emplois non agricoles (NFP)"
    if "unemployment" in key:
        return "Taux de chômage"
    if "retail sales" in key:
        if "core" in key:
            return "Ventes au détail hors éléments volatils"
        return "Ventes au détail"
    if "gdp" in key:
        if "flash" in key:
            return "PIB flash"
        if "prelim" in key:
            return "PIB préliminaire"
        if "final" in key:
            return "PIB final"
        return "PIB"
    if "consumer confidence" in key:
        return "Confiance des consommateurs"
    if "consumer sentiment" in key:
        return "Sentiment des consommateurs"
    if "pmi" in key:
        if "manufacturing" in key:
            return "PMI manufacturier"
        if "services" in key or "service" in key:
            return "PMI services"
        return "PMI"
    if "cpi" in key:
        if "core" in key:
            if "y/y" in key:
                return "Inflation sous-jacente CPI (annuelle)"
            if "m/m" in key:
                return "Inflation sous-jacente CPI (mensuelle)"
            return "Inflation sous-jacente CPI"
        if "y/y" in key:
            return "Inflation CPI (annuelle)"
        if "m/m" in key:
            return "Inflation CPI (mensuelle)"
        return "Inflation CPI"
    if "ppi" in key:
        if "core" in key:
            if "y/y" in key:
                return "Inflation sous-jacente PPI (annuelle)"
            if "m/m" in key:
                return "Inflation sous-jacente PPI (mensuelle)"
            return "Inflation sous-jacente PPI"
        if "y/y" in key:
            return "Inflation PPI (annuelle)"
        if "m/m" in key:
            return "Inflation PPI (mensuelle)"
        return "Inflation PPI"
    if "pce" in key:
        if "core" in key:
            if "y/y" in key:
                return "Indice PCE sous-jacent (annuel)"
            if "m/m" in key:
                return "Indice PCE sous-jacent (mensuel)"
            return "Indice PCE sous-jacent"
        if "y/y" in key:
            return "Indice PCE (annuel)"
        if "m/m" in key:
            return "Indice PCE (mensuel)"
        return "Indice PCE"
    if "home sales" in key:
        if "pending" in key:
            return "Promesses de ventes immobilières"
        if "existing" in key:
            return "Ventes de logements existants"
        if "new" in key:
            return "Ventes de logements neufs"
        return "Ventes immobilières"
    if "durable goods" in key:
        if "core" in key:
            return "Commandes de biens durables hors transport"
        return "Commandes de biens durables"
    if "crude oil inventories" in key:
        return "Stocks hebdomadaires de pétrole brut"
    if "building permits" in key:
        return "Permis de construire"
    if "housing starts" in key:
        return "Mises en chantier"
    if "trade balance" in key:
        return "Balance commerciale"
    if "current account" in key:
        return "Balance des paiements courants"

    return title.strip()
   
def event_priority_icon(title: str, impact: str) -> str:
    t = normalize_event_title(title)

    # priorité maximale : banques centrales / discours
    if (
        "fomc" in t
        or "federal funds rate" in t
        or "interest rate" in t
        or "main refinancing rate" in t
        or "press conference" in t
        or "speech" in t
        or "speaks" in t
        or "testifies" in t
        or "hearing" in t
    ):
        return "🔥"

    # très haute priorité : inflation / emploi / croissance
    if (
        "cpi" in t
        or "ppi" in t
        or "pce" in t
        or "non farm payroll" in t
        or t == "nfp"
        or "unemployment" in t
        or "gdp" in t
        or "retail sales" in t
    ):
        return "🚨"

    # high impact générique
    if impact.upper() == "HIGH":
        return "⚠️"

    return "📌"


def event_sort_priority(title: str, impact: str) -> int:
    t = normalize_event_title(title)

    if (
        "fomc" in t
        or "federal funds rate" in t
        or "interest rate" in t
        or "main refinancing rate" in t
        or "press conference" in t
        or "speech" in t
        or "speaks" in t
        or "testifies" in t
        or "hearing" in t
    ):
        return 0

    if (
        "cpi" in t
        or "ppi" in t
        or "pce" in t
        or "non farm payroll" in t
        or t == "nfp"
        or "unemployment" in t
        or "gdp" in t
        or "retail sales" in t
    ):
        return 1

    if impact.upper() == "HIGH":
        return 2

    return 3   

def load_state() -> dict:
    default_state = {
        "sent_reminders": {},
        "sent_daily": {},
        "seen_events": [],
        "sent_releases": {},
        "source_failures": 0,
        "last_source_alert": None,
    }

    if not STATE_FILE.exists():
        return default_state

    try:
        content = STATE_FILE.read_text(encoding="utf-8").strip()
        if not content:
            return default_state
        return json.loads(content)
    except Exception:
        return default_state


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
    return FLAGS.get(cur, "🏳️")


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
    impact = ev["impact"]
    title = ev["title"]

    title_fr = smart_translate_event(title)
    icon = event_priority_icon(title, impact)

    forecast = ev.get("forecast")
    previous = ev.get("previous")

    values = ""
    if forecast:
        values += f"\n📊 Prévision : {forecast}"
    if previous:
        values += f"\n📊 Précédent : {previous}"

    assets = relevant_assets_for_event(ev)
    assets_block = "\n".join(f"• {a}" for a in assets) if assets else "• (aucun)"

    return (
        f"{icon} ALERTE MACRO\n\n"
        f"⏰ Dans {minutes_left} min — {dt_local.strftime('%H:%M')} (Paris)\n\n"
        f"{flag_for_currency(cur)} {cur}\n"
        f"{title_fr}\n"
        f"({title})"
        f"{values}\n\n"
        f"Actifs concernés\n{assets_block}"
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
    title_fr = smart_translate_event(title)

    actual = ev.get("actual")
    forecast = ev.get("forecast")
    previous = ev.get("previous")

    surprise = compute_surprise(actual, forecast)

    # ignorer les surprises trop faibles
    if surprise is not None and abs(surprise) < 0.02:
        surprise = 0

    surprise_text = ""
    impact_text = "➖ Impact macro : neutre"

    if surprise is not None:

        surprise_text = f"\n⚡ Surprise : {surprise*100:+.2f}%"

        direction = 1

        for key in MACRO_DIRECTION:
            if key.lower() in title.lower():
                direction = MACRO_DIRECTION[key]
                break

        macro_effect = surprise * direction

        if macro_effect > 0:
            impact_text = f"📈 Impact macro : {cur} bullish"
        elif macro_effect < 0:
            impact_text = f"📉 Impact macro : {cur} bearish"

    return (
        "🚨 DONNÉE MACRO PUBLIÉE\n\n"
        f"{flag_for_currency(cur)} {cur}\n"
        f"{title_fr}\n"
        f"({title})\n\n"
        f"Réel : {actual}\n"
        f"Prévision : {forecast}\n"
        f"Précédent : {previous}"
        f"{surprise_text}\n\n"
        f"{impact_text}\n\n"
        f"🕒 {dt_local.strftime('%H:%M')} (Paris)"
    )


def format_daily_summary(
    day: datetime.date, events: list[tuple[datetime, dict]]
) -> str:
    header = f"🗓️ Macro de demain — {day.strftime('%d/%m/%Y')}\n\n"

    day_events = []
    for dt_local, ev in sorted(events, key=lambda x: x[0]):
        if dt_local.date() != day:
            continue
        day_events.append((dt_local, ev))

    if not day_events:
        return header + "Aucune annonce pertinente."

    day_events.sort(key=lambda x: x[0])

    lines = []
    for dt_local, ev in day_events:
        cur = ev["country"]
        impact = ev["impact"]
        title = ev["title"]

        title_fr = smart_translate_event(title)
        icon = event_priority_icon(title, impact)

        assets = relevant_assets_for_event(ev)
        assets_str = ", ".join(assets) if assets else "-"

        line = (
            f"{icon} {dt_local.strftime('%H:%M')} {cur} "
            f"{title_fr} ({title}) ({assets_str})"
        )

        lines.append(line)

    legend = (
        "Légende : 🔥 priorité max | 🚨 très important | ⚠️ impact élevé | 📌 secondaire\n\n"
    )

    return header + legend + "\n".join(lines)


def event_key(dt, ev):
    title = ev["title"].strip().lower()
    return f"{dt.isoformat()}::{ev['country']}::{title}"


def main():
    state = load_state()
    
    print("CWD:", os.getcwd())
    print("STATE PATH:", STATE_FILE.resolve())

    # sécurité structure state.json
    state.setdefault("sent_releases", {})
    state.setdefault("sent_reminders", {})
    state.setdefault("sent_daily", {})
    state.setdefault("seen_events", [])

    now = datetime.now(TZ)

    # 1) Récupération events avec fallback + monitoring
    try:
        events = fetch_events()

        # Détection des nouvelles annonces apparues en cours de route
        seen = set(state.get("seen_events", []))

        for dt, ev in events:
            key = event_key(dt, ev)

            # On n'alerte que si l'événement est nouveau
            if key not in seen:
                # On évite de notifier des vieilleries déjà passées depuis longtemps
                if dt >= now - timedelta(minutes=10):

                    should_alert_new = False

                    # alerte immédiate si high impact
                    if ev["impact"] == "High":
                        should_alert_new = True

                    # alerte immédiate si événement critique type speech / conference
                    if is_critical_event(ev["title"]):
                        should_alert_new = True

                    if should_alert_new:
                        impact_label = (
                            "🚨 HIGH IMPACT"
                            if ev["impact"] == "High"
                            else "⚠️ NOUVEL ÉVÉNEMENT"
                        )
                        title_fr = smart_translate_event(ev["title"])

                        msg = (
                            "🆕 ANNONCE AJOUTÉE EN COURS DE JOURNÉE\n\n"
                            f"{impact_label}\n\n"
                            f"{flag_for_currency(ev['country'])} {ev['country']}\n"
                            f"{title_fr}\n"
                            f"({ev['title']})\n\n"
                            f"📅 {dt.strftime('%d/%m')}\n"
                            f"🕒 {dt.strftime('%H:%M')} (Paris)"
                        )

                        if is_critical_event(ev["title"]):
                            msg += "\n\n🔥 Événement potentiellement très volatil."

                        tg_send(msg)

                seen.add(key)

        state["seen_events"] = list(seen)[-300:]
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
        # on autorise aussi les releases même si peu d'actifs
        if not is_relevant_event(ev) and ev["impact"] != "High":
            continue

        reminder_time = dt - timedelta(minutes=REMINDER_LEAD_MIN)
        key = event_key(dt, ev)

        # ----- REMINDER -----
        if reminder_time <= now < dt:
            if key not in state["sent_reminders"]:
                minutes_left = max(0, int((dt - now).total_seconds() / 60))
                msg = format_macro_alert(dt, ev, minutes_left)
                tg_send(msg)
                state["sent_reminders"][key] = now.isoformat()

        # ----- RELEASE -----
        if now < dt:
            continue
        if (now - dt).total_seconds() > 600:
            continue

        actual = ev.get("actual")

        key_release = event_key(dt, ev)

        # créer la structure si elle n'existe pas
        state.setdefault("sent_releases", {})

        # si déjà envoyée → ne rien faire
        if key_release in state["sent_releases"]:
            continue

        # si la donnée existe → envoyer
        if actual and actual.strip() and actual != "-":
            msg = format_release_alert(dt, ev)
            tg_send(msg)

            # mémoriser immédiatement
            state["sent_releases"][key_release] = now.isoformat()

            save_state(state)

            continue
    save_state(state)


if __name__ == "__main__":
    main()
